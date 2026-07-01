import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from base import run_scraper
from bs4 import BeautifulSoup

DOMAIN = "newsweek.com"


def _extract_images(soup):
    images = []
    seen_bases = set()
    ld_meta = {}
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
            if isinstance(data, list):
                data = data[0]
            if isinstance(data, dict) and "image" in data:
                img_list = data["image"] if isinstance(data["image"], list) else [data["image"]]
                for img_item in img_list:
                    if not isinstance(img_item, dict):
                        continue
                    img_url = img_item.get("url", "") or img_item.get("contentUrl", "")
                    base = img_url.split("?")[0]
                    if base and base not in ld_meta:
                        ld_meta[base] = {
                            "caption": img_item.get("caption", ""),
                            "credit": img_item.get("creditText", ""),
                        }
        except Exception:
            pass
    article = soup.find("article")
    if article:
        for img in article.find_all("img"):
            src = img.get("src", "")
            if not src or not src.startswith("http"):
                continue
            if "gravatar.com" in src:
                continue
            base = src.split("?")[0]
            if base in seen_bases:
                continue
            seen_bases.add(base)
            alt = img.get("alt", "")
            entry = {"url": base, "alt": alt}
            meta = ld_meta.get(base, {})
            if meta.get("caption"):
                entry["caption"] = meta["caption"]
            if meta.get("credit"):
                entry["credit"] = meta["credit"]
            images.append(entry)
    for base, meta in ld_meta.items():
        if base not in seen_bases:
            seen_bases.add(base)
            entry = {"url": base, "alt": ""}
            if meta.get("caption"):
                entry["caption"] = meta["caption"]
            if meta.get("credit"):
                entry["credit"] = meta["credit"]
            images.insert(0, entry)
    return images


def parse(html, url):
    soup = BeautifulSoup(html, "html.parser")

    h1 = soup.find("h1")
    headline = h1.get_text(strip=True) if h1 else ""

    images = _extract_images(soup)

    article = soup.find("article")
    if article:
        parts = []
        for el in article.find_all(["h2", "h3", "p"]):
            if el.find_parent(class_=lambda c: c and "Read More" in str(c)):
                continue
            if el.name in ("h2", "h3"):
                text = el.get_text(strip=True)
                if text and not text.startswith("Read More"):
                    parts.append(f"\n## {text}\n")
            elif el.name == "p":
                text = el.get_text(" ", strip=True)
                if len(text) >= 20:
                    parts.append(text)
        body = "\n\n".join(parts)
        if body:
            return headline, body, images

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
            if isinstance(data, list):
                data = data[0]
            if isinstance(data, dict) and data.get("articleBody"):
                return data.get("headline", headline), data["articleBody"], images
        except Exception:
            pass

    paras = soup.select("article p, main p")
    body = "\n\n".join(p.get_text(" ", strip=True) for p in paras if len(p.get_text(strip=True)) > 20)
    return headline, body, images


if __name__ == "__main__":
    run_scraper(DOMAIN, parse)
