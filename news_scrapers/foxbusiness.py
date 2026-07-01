import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from base import run_scraper
from bs4 import BeautifulSoup

DOMAIN = "foxbusiness.com"

SKIP_IMG_EXACT = {"532x120"}
SKIP_IMG_SEGMENT = {"logo", "icon", "newsletter", "ads", "pixel", "tracker"}
GENERIC_OG = {"og-fox-business.png", "og-foxbusiness.png", "og-fox-news.png"}


def _should_skip_img(src):
    low = src.lower()
    if any(s in low for s in SKIP_IMG_EXACT):
        return True
    segments = low.replace("\\", "/").split("/")
    return any(seg in SKIP_IMG_SEGMENT for seg in segments)


def _img_key(url):
    """Normalize URL to a dedup key: extract filename from the path."""
    path = url.split("?")[0].split("#")[0]
    return path.split("/")[-1].lower()


def _extract_images(soup):
    images = []
    seen_keys = set()

    # 1) <img> and <amp-img> tags inside the article body (these have captions)
    article = soup.find("article") or soup
    for img in article.find_all(["img", "amp-img"]):
        if img.find_parent(class_="author-headshot"):
            continue
        src = img.get("src", "") or ""
        if not src.startswith("http") or "data:image" in src or "clear.gif" in src:
            continue
        if _should_skip_img(src):
            continue
        key = _img_key(src)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        alt = img.get("alt", "")
        caption = ""
        ancestor = img.parent
        while ancestor and ancestor.name not in ("article", "[document]", None):
            cap_el = ancestor.find(class_="caption")
            if cap_el:
                caption = cap_el.get_text(strip=True)
                break
            ancestor = ancestor.parent
        images.append({"url": src, "alt": alt, "caption": caption})

    # 2) og:image — hero image (JS-rendered, not in <article>)
    og = soup.find("meta", property="og:image")
    if og:
        og_url = og.get("content", "")
        if og_url:
            if not og_url.startswith("http"):
                og_url = "https:" + og_url
            key = _img_key(og_url)
            if key not in GENERIC_OG and key not in seen_keys:
                seen_keys.add(key)
                images.insert(0, {"url": og_url, "alt": "", "caption": ""})

    # 3) LD+JSON image
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
            items = [data] if isinstance(data, dict) else (data if isinstance(data, list) else [])
            for d in items:
                if not isinstance(d, dict):
                    continue
                img_val = d.get("image")
                if isinstance(img_val, dict):
                    img_val = img_val.get("url", "")
                if isinstance(img_val, str) and img_val.startswith("http"):
                    key = _img_key(img_val)
                    if key not in seen_keys and key not in GENERIC_OG:
                        seen_keys.add(key)
                        images.insert(0, {"url": img_val, "alt": "", "caption": ""})
        except Exception:
            pass

    return images


def parse(html, url):
    soup = BeautifulSoup(html, "html.parser")

    h1 = soup.select_one("h1.headline") or soup.select_one("h1")
    headline = h1.get_text(strip=True) if h1 else ""

    images = _extract_images(soup)

    paras = soup.select("div.article-body > p")
    body = "\n\n".join(p.get_text(strip=True) for p in paras if len(p.get_text(strip=True)) > 20)
    if body:
        return headline, body, images

    paras = soup.select("article p")
    body = "\n\n".join(p.get_text(strip=True) for p in paras if len(p.get_text(strip=True)) > 20)
    if body:
        return headline, body, images

    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string)
            if isinstance(data, dict) and data.get("articleBody"):
                return data.get("headline", headline), data["articleBody"], images
            if isinstance(data, list):
                for d in data:
                    if isinstance(d, dict) and d.get("articleBody"):
                        return d.get("headline", headline), d["articleBody"], images
        except Exception:
            pass

    return headline, body, images

if __name__ == "__main__":
    run_scraper(DOMAIN, parse)
