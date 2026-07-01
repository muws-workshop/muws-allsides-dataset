import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from base import run_scraper
from bs4 import BeautifulSoup

DOMAIN = "foxnews.com"


def _extract_images(soup):
    images = []
    seen = set()
    body_div = soup.find("div", class_="article-body")
    if body_div:
        for ct in body_div.find_all("div", class_=lambda c: c and "image-ct" in str(c)):
            img = ct.find("img")
            if img:
                src = img.get("src", "")
                if src and src.startswith("http") and src not in seen:
                    seen.add(src)
                    images.append({"url": src, "alt": img.get("alt", "")})
    og = soup.find("meta", property="og:image")
    if og:
        src = og.get("content", "")
        if src and src not in seen:
            seen.add(src)
            images.insert(0, {"url": src, "alt": ""})
    return images


def _extract_videos(soup):
    videos = []
    feat = soup.find("div", class_=lambda c: c and "featured-video" in str(c))
    if feat:
        img = feat.find("img")
        thumbnail = img.get("src", "") if img else ""
        title = img.get("alt", "") if img else ""
        if thumbnail:
            videos.append({"type": "featured_video", "thumbnail": thumbnail, "title": title})
    return videos


def parse(html, url):
    soup = BeautifulSoup(html, "html.parser")

    h1 = soup.find("h1", class_="headline")
    if not h1:
        h1 = soup.find("h1")
    headline = h1.get_text(strip=True) if h1 else ""

    images = _extract_images(soup)
    videos = _extract_videos(soup)

    body_div = soup.find("div", class_="article-body")
    if body_div:
        parts = []
        for child in body_div.children:
            if not hasattr(child, "name") or not child.name:
                continue
            if child.name in ("h2", "h3", "h4"):
                text = child.get_text(strip=True)
                if text:
                    parts.append(f"\n## {text}\n")
            elif child.name == "p":
                text = child.get_text(" ", strip=True)
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
