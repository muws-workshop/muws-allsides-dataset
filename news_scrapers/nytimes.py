import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from base import run_scraper
from bs4 import BeautifulSoup

DOMAIN = "nytimes.com"
EXTRA_HEADERS = {"Referer": "https://www.google.com/"}


def _extract_images(soup):
    images = []
    seen = set()
    article = soup.find("article")
    if article:
        for fig in article.find_all("figure"):
            img = fig.find("img")
            if img:
                src = img.get("src", "")
                if src and src.startswith("http") and src not in seen:
                    seen.add(src)
                    alt = img.get("alt", "")
                    cap = fig.find("figcaption")
                    caption = cap.get_text(strip=True) if cap else ""
                    images.append({"url": src, "alt": alt, "caption": caption})
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
            if isinstance(data, list):
                data = data[0]
            if isinstance(data, dict) and "image" in data:
                img_data = data["image"]
                if isinstance(img_data, list):
                    for item in img_data:
                        img_url = item.get("url", "") if isinstance(item, dict) else str(item)
                        if img_url and img_url not in seen:
                            seen.add(img_url)
                            images.append({"url": img_url, "alt": ""})
                elif isinstance(img_data, dict):
                    img_url = img_data.get("url", "")
                    if img_url and img_url not in seen:
                        seen.add(img_url)
                        images.insert(0, {"url": img_url, "alt": ""})
                elif isinstance(img_data, str) and img_data not in seen:
                    seen.add(img_data)
                    images.insert(0, {"url": img_data, "alt": ""})
        except Exception:
            pass
    if not images:
        og = soup.find("meta", property="og:image")
        if og:
            src = og.get("content", "")
            if src:
                images.append({"url": src, "alt": ""})
    return images


def parse(html, url):
    soup = BeautifulSoup(html, "html.parser")

    h1 = soup.find("h1")
    headline = h1.get_text(strip=True) if h1 else ""

    images = _extract_images(soup)

    # LD+JSON articleBody is preferred for NYT (often behind paywall otherwise)
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
            if isinstance(data, list):
                data = data[0]
            if isinstance(data, dict):
                body = data.get("articleBody", "")
                if body and len(body) > 200:
                    return headline or data.get("headline", ""), body, images
        except Exception:
            pass

    article = soup.find("article")
    if article:
        parts = []
        for child in article.find_all(["p", "h2", "h3"]):
            if child.name in ("h2", "h3"):
                text = child.get_text(strip=True)
                if text and len(text) > 3:
                    parts.append(f"\n## {text}\n")
            elif child.name == "p":
                text = child.get_text(" ", strip=True)
                if len(text) >= 30:
                    parts.append(text)
        body = "\n\n".join(parts)
        if body and len(body) > 200:
            return headline, body, images

    paras = soup.select("article p, main p")
    body = "\n\n".join(p.get_text(" ", strip=True) for p in paras if len(p.get_text(strip=True)) > 20)
    return headline, body, images


if __name__ == "__main__":
    run_scraper(DOMAIN, parse, extra_headers=EXTRA_HEADERS)
