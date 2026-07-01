import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from base import run_scraper
from bs4 import BeautifulSoup

DOMAIN = "thehill.com"


def _extract_images(soup):
    images = []
    seen = set()
    feat = soup.find(class_="article__featured-media")
    if feat:
        img = feat.find("img")
        if img:
            src = img.get("src", "")
            if src and src.startswith("http") and src not in seen:
                seen.add(src)
                images.append({"url": src, "alt": img.get("alt", "")})
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
            if isinstance(data, list):
                data = data[0]
            if isinstance(data, dict) and "image" in data:
                img_data = data["image"]
                img_url = img_data.get("url", "") if isinstance(img_data, dict) else str(img_data)
                if img_url and img_url not in seen:
                    seen.add(img_url)
                    images.insert(0, {"url": img_url, "alt": ""})
        except Exception:
            pass
    if not images:
        og = soup.find("meta", property="og:image")
        if og:
            src = og.get("content", "")
            if src:
                images.append({"url": src, "alt": ""})
    return images


def _extract_videos(soup):
    videos = []
    for div in soup.find_all("div", class_=lambda c: c and "nexstar-video" in str(c)):
        loc = div.get("data-player-location", "")
        if loc:
            videos.append({"type": "nexstar_video", "location": loc})
    return videos


def parse(html, url):
    soup = BeautifulSoup(html, "html.parser")

    h1 = soup.find("h1", class_="page-title")
    if not h1:
        h1 = soup.find("h1")
    headline = h1.get_text(strip=True) if h1 else ""

    images = _extract_images(soup)
    videos = _extract_videos(soup)

    body_div = soup.find(class_="article__text")
    if body_div:
        parts = []
        for el in body_div.find_all(["h2", "h3", "h4", "p"]):
            if el.name in ("h2", "h3", "h4"):
                text = el.get_text(strip=True)
                if text:
                    parts.append(f"\n## {text}\n")
            elif el.name == "p":
                text = el.get_text(" ", strip=True)
                if len(text) >= 20:
                    parts.append(text)
        body = "\n\n".join(parts)
        if body:
            return headline, body, images, videos, []

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
            if isinstance(data, list):
                data = data[0]
            if isinstance(data, dict) and data.get("articleBody"):
                return data.get("headline", headline), data["articleBody"], images, videos, []
        except Exception:
            pass

    paras = soup.select("article p")
    body = "\n\n".join(p.get_text(" ", strip=True) for p in paras if len(p.get_text(strip=True)) > 20)
    return headline, body, images, videos, []


if __name__ == "__main__":
    run_scraper(DOMAIN, parse)
