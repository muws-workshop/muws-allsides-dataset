import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from base import run_scraper
from bs4 import BeautifulSoup

DOMAIN = "nypost.com"


def _extract_images(soup):
    images = []
    seen = set()
    body_div = soup.find(class_=lambda c: c and "single__content" in str(c))
    if body_div:
        for fig in body_div.find_all("figure"):
            if fig.find_parent(class_=lambda c: c and "inline-module" in str(c)):
                continue
            img = fig.find("img")
            if img:
                src = img.get("src", "")
                if src and src.startswith("http") and src not in seen:
                    seen.add(src)
                    alt = img.get("alt", "")
                    cap = fig.find("figcaption")
                    caption = cap.get_text(strip=True) if cap else ""
                    images.append({"url": src, "alt": alt, "caption": caption})
    og = soup.find("meta", property="og:image")
    if og:
        src = og.get("content", "")
        if src and src not in seen:
            seen.add(src)
            images.insert(0, {"url": src, "alt": ""})
    return images


def _extract_videos(soup):
    videos = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
            if isinstance(data, list):
                data = data[0]
            if isinstance(data, dict) and data.get("@type") == "VideoObject":
                videos.append({
                    "type": "video",
                    "title": data.get("name", ""),
                    "url": data.get("embedUrl", "") or data.get("contentUrl", ""),
                    "thumbnail": data.get("thumbnailUrl", ""),
                })
            elif isinstance(data, dict) and "video" in data:
                vdata = data["video"]
                if isinstance(vdata, dict):
                    videos.append({
                        "type": "video",
                        "title": vdata.get("name", ""),
                        "url": vdata.get("embedUrl", "") or vdata.get("contentUrl", ""),
                        "thumbnail": vdata.get("thumbnailUrl", ""),
                    })
        except Exception:
            pass
    for iframe in soup.find_all("iframe"):
        src = iframe.get("src", "")
        if src and "embeds.nypost.com" in src and src not in [v.get("url") for v in videos]:
            videos.append({"type": "embed", "url": src, "title": ""})
    return videos


def parse(html, url):
    soup = BeautifulSoup(html, "html.parser")

    h1 = soup.find("h1")
    headline = h1.get_text(strip=True) if h1 else ""

    images = _extract_images(soup)
    videos = _extract_videos(soup)

    body_div = soup.find(class_=lambda c: c and "single__content" in str(c))
    if body_div:
        parts = []
        for child in body_div.children:
            if not hasattr(child, "name") or not child.name:
                continue
            classes = " ".join(child.get("class", []))
            if "inline-module" in classes or "inline_module" in classes:
                continue
            if child.name in ("h2", "h3"):
                text = child.get_text(strip=True)
                if text and text != "Explore More":
                    parts.append(f"\n## {text}\n")
            elif child.name == "p":
                text = child.get_text(" ", strip=True)
                if len(text) >= 20:
                    parts.append(text)
        body = "\n\n".join(parts)
        if body:
            return headline, body, images, videos, []

    article = soup.find("article")
    if article:
        parts = []
        for p in article.find_all("p"):
            text = p.get_text(" ", strip=True)
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

    return headline, "", images, videos, []


if __name__ == "__main__":
    run_scraper(DOMAIN, parse)
