import sys, os, json, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from base import run_scraper
from bs4 import BeautifulSoup

DOMAIN = "theguardian.com"

_MEDIA_HASH_RE = re.compile(r'/img/media/([a-f0-9]{40})/')


def _img_key(url):
    m = _MEDIA_HASH_RE.search(url)
    return m.group(1) if m else url.split("?")[0]


def _extract_images(soup):
    images = []
    seen = set()
    article = soup.find("article")
    scope = article if article else soup

    for fig in scope.find_all("figure"):
        img = fig.find("img")
        if not img:
            pic = fig.find("picture")
            if pic:
                img = pic.find("img")
        if img:
            src = img.get("src", "")
            key = _img_key(src) if src else ""
            if src and src.startswith("http") and key not in seen:
                seen.add(key)
                alt = img.get("alt", "")
                cap = fig.find("figcaption")
                caption = cap.get_text(" ", strip=True) if cap else ""
                images.append({"url": src, "alt": alt, "caption": caption})

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
            if isinstance(data, list):
                data = data[0]
            if isinstance(data, dict) and "image" in data:
                img_data = data["image"]
                items = img_data if isinstance(img_data, list) else [img_data]
                for item in items:
                    img_url = item.get("url", "") if isinstance(item, dict) else str(item)
                    key = _img_key(img_url) if img_url else ""
                    if img_url and key not in seen:
                        seen.add(key)
                        images.append({"url": img_url, "alt": ""})
        except Exception:
            pass

    if not images:
        og = soup.find("meta", property="og:image")
        if og:
            src = og.get("content", "")
            if src:
                og_alt = soup.find("meta", property="og:image:alt")
                alt = og_alt.get("content", "") if og_alt else ""
                og_desc = soup.find("meta", property="og:description")
                caption = og_desc.get("content", "") if og_desc else ""
                images.append({"url": src, "alt": alt, "caption": caption})

    return images


def parse(html, url):
    soup = BeautifulSoup(html, "html.parser")

    h1 = soup.select_one("h1")
    headline = h1.get_text(strip=True) if h1 else ""

    images = _extract_images(soup)

    body_div = soup.select_one("div.article-body-commercial-selector")
    if body_div:
        parts = []
        for child in body_div.children:
            if not hasattr(child, "name") or not child.name:
                continue
            if child.name in ("h2", "h3"):
                text = child.get_text(strip=True)
                if text:
                    parts.append(f"\n## {text}\n")
            elif child.name == "p":
                text = child.get_text(" ", strip=True)
                if len(text) >= 20:
                    parts.append(text)
        body = "\n\n".join(parts)
        if body:
            return headline, body, images

    paras = soup.select("article p")
    if paras:
        body = "\n\n".join(p.get_text(" ", strip=True) for p in paras if len(p.get_text(strip=True)) > 20)
        if body:
            return headline, body, images

    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string)
            if isinstance(data, dict) and data.get("articleBody"):
                return data.get("headline", headline), data["articleBody"], images
        except Exception:
            pass

    return headline, "", images

if __name__ == "__main__":
    run_scraper(DOMAIN, parse)
