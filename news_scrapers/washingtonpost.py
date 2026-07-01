import sys, os, json
from urllib.parse import urlparse, parse_qs
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from base import run_scraper
from bs4 import BeautifulSoup

DOMAIN = "washingtonpost.com"
EXTRA_HEADERS = {"Referer": "https://www.google.com/"}


def _wapo_base_url(url):
    """Normalize WaPo imrs.php resized URLs to the underlying S3 base URL."""
    parsed = urlparse(url)
    if "imrs.php" in parsed.path:
        src = parse_qs(parsed.query).get("src", [""])[0]
        if src:
            return src
    return url


def _img_src(img_tag):
    """Extract the best image URL from an <img> tag."""
    src = img_tag.get("src", "")
    if not src:
        srcset = img_tag.get("srcSet", "") or img_tag.get("srcset", "")
        if srcset:
            src = srcset.split(",")[-1].strip().split()[0]
    return src


def _html_captions(soup):
    """Extract captions from HTML figcaption elements, keyed by base image URL."""
    captions = {}
    lede_cap_el = soup.find(attrs={"data-testid": "lede-art-caption"})
    lede_caption = lede_cap_el.get_text(strip=True) if lede_cap_el else ""

    for fig in soup.find_all("figure"):
        img = fig.find("img")
        if not img:
            continue
        src = _img_src(img)
        if not src:
            continue
        base = _wapo_base_url(src)
        cap_el = fig.find("figcaption")
        if not cap_el:
            cap_el = fig.find_next_sibling("figcaption")
        if cap_el:
            captions[base] = cap_el.get_text(strip=True)
        elif fig.get("data-testid") == "lede-image" and lede_caption:
            captions[base] = lede_caption

    return captions


def _extract_images(soup):
    images = []
    seen = set()

    def _add(url, alt="", caption=""):
        base = _wapo_base_url(url)
        if base in seen:
            return
        seen.add(base)
        images.append({"url": base, "alt": alt, "caption": caption})

    # Collect captions from HTML up front (works regardless of data source)
    html_caps = _html_captions(soup)

    # WaPo uses __NEXT_DATA__ with Arc content_elements
    next_data = soup.find("script", id="__NEXT_DATA__")
    if next_data:
        try:
            data = json.loads(next_data.string)
            gc = data.get("props", {}).get("pageProps", {}).get("globalContent", {})
            promo = gc.get("promo_items", {}).get("basic", {})
            if promo.get("type") == "image":
                _add(
                    promo.get("url", ""),
                    alt=promo.get("alt_text", ""),
                    caption=promo.get("caption", ""),
                )
            for el in gc.get("content_elements", []):
                if el.get("type") == "image":
                    _add(
                        el.get("url", ""),
                        alt=el.get("alt_text", ""),
                        caption=el.get("caption", "") or el.get("subtitle", ""),
                    )
        except Exception:
            pass

    # HTML figure extraction fallback (when __NEXT_DATA__ is absent)
    if not images:
        for fig in soup.find_all("figure"):
            img = fig.find("img")
            if not img:
                continue
            src = _img_src(img)
            if not src:
                continue
            _add(src, alt=img.get("alt", ""))

    # ld+json fallback (deduplicates via base URL)
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
            items = data if isinstance(data, list) else [data]
            for d in items:
                if isinstance(d, dict) and "image" in d:
                    img_data = d["image"]
                    img_items = img_data if isinstance(img_data, list) else [img_data]
                    for item in img_items:
                        img_url = item.get("url", "") if isinstance(item, dict) else str(item)
                        if img_url:
                            _add(img_url)
        except Exception:
            pass

    if not images:
        og = soup.find("meta", property="og:image")
        if og:
            src = og.get("content", "")
            if src:
                _add(src)

    # Enrich: fill in captions from HTML for images that have none
    for img in images:
        if not img.get("caption"):
            base = _wapo_base_url(img["url"])
            if base in html_caps:
                img["caption"] = html_caps[base]

    return images


_NAV_GARBAGE_MARKERS = [
    "WP Intelligence operates independently",
    "Stephen Gutowski\n Adam O'Neal",
]


def _is_nav_garbage(text):
    """Detect WaPo sidebar/navigation text that trafilatura sometimes extracts."""
    return any(m in text for m in _NAV_GARBAGE_MARKERS)


def parse(html, url):
    soup = BeautifulSoup(html, "html.parser")

    h1 = soup.select_one("h1")
    headline = h1.get_text(strip=True) if h1 else ""

    images = _extract_images(soup)

    # Try __NEXT_DATA__ content_elements first (bypasses JS rendering)
    next_data = soup.find("script", id="__NEXT_DATA__")
    if next_data:
        try:
            data = json.loads(next_data.string)
            gc = data.get("props", {}).get("pageProps", {}).get("globalContent", {})
            ce = gc.get("content_elements", [])
            parts = []
            for el in ce:
                el_type = el.get("type", "")
                if el_type == "text":
                    content = el.get("content", "")
                    if content:
                        text_soup = BeautifulSoup(content, "html.parser")
                        text = text_soup.get_text(" ", strip=True)
                        if len(text) >= 20:
                            parts.append(text)
                elif el_type == "header":
                    content = el.get("content", "")
                    if content:
                        text_soup = BeautifulSoup(content, "html.parser")
                        text = text_soup.get_text(strip=True)
                        if text:
                            parts.append(f"\n## {text}\n")
            if parts:
                if not headline:
                    headline = gc.get("headlines", {}).get("basic", "")
                return headline, "\n\n".join(parts), images
        except Exception:
            pass

    # Fallback: HTML selectors
    for selector in ["div[data-qa='article-body'] p", "div.article-body p", "article p"]:
        paras = soup.select(selector)
        if paras:
            body = "\n\n".join(
                p.get_text(" ", strip=True) for p in paras
                if len(p.get_text(strip=True)) > 20
            )
            if body and not _is_nav_garbage(body):
                return headline, body, images

    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string)
            if isinstance(data, dict) and data.get("articleBody"):
                ab = data["articleBody"]
                if not _is_nav_garbage(ab):
                    return data.get("headline", headline), ab, images
        except Exception:
            pass

    return headline, "", images

def _body_filter(body):
    """Reject body text that is actually WaPo navigation/sidebar chrome."""
    return not _is_nav_garbage(body)


if __name__ == "__main__":
    run_scraper(DOMAIN, parse, extra_headers=EXTRA_HEADERS, body_filter=_body_filter)
