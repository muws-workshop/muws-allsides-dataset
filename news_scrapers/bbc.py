import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from base import run_scraper
from bs4 import BeautifulSoup

DOMAIN = "bbc.com"


def _extract_images(article):
    images = []
    seen = set()
    for div in article.find_all("div", attrs={"data-component": "image-block"}):
        fig = div.find("figure")
        if not fig:
            continue
        real_img = None
        for img in fig.find_all("img"):
            src = img.get("src", "")
            if "ichef.bbci.co.uk" in src:
                real_img = img
                break
        if not real_img:
            continue
        src = real_img.get("src", "")
        if not src or src in seen:
            continue
        seen.add(src)
        alt = real_img.get("alt", "")
        caption = ""
        cap_el = fig.find("figcaption")
        if cap_el:
            caption = cap_el.get_text(strip=True)
        images.append({"url": src, "alt": alt, "caption": caption})
    return images


def parse(html, url):
    soup = BeautifulSoup(html, "html.parser")

    h1 = soup.select_one("h1")
    headline = h1.get_text(strip=True) if h1 else ""

    article = soup.find("article")
    if article:
        images = _extract_images(article)
        parts = []
        for div in article.find_all("div", attrs={"data-component": True}):
            comp = div.get("data-component")
            if comp == "subheadline-block":
                h = div.find(["h2", "h3", "h4"])
                if h:
                    level = int(h.name[1])
                    text = h.get_text(strip=True)
                    if text:
                        parts.append(f"\n{'#' * level} {text}\n")
            elif comp == "text-block":
                p = div.find("p")
                if p:
                    text = p.get_text(" ", strip=True)
                    if len(text) >= 20:
                        parts.append(text)
        body = "\n\n".join(parts)
        if body:
            return headline, body, images

    images = []

    paras = soup.select("article p")
    body = "\n\n".join(p.get_text(strip=True) for p in paras if len(p.get_text(strip=True)) > 20)
    if body:
        return headline, body, images

    paras = soup.select("main p")
    body = "\n\n".join(p.get_text(strip=True) for p in paras if len(p.get_text(strip=True)) > 20)
    if body:
        return headline, body, images

    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string)
            if isinstance(data, dict) and data.get("articleBody"):
                return data.get("headline", headline), data["articleBody"], images
        except Exception:
            pass

    return headline, body, images

if __name__ == "__main__":
    run_scraper(DOMAIN, parse)
