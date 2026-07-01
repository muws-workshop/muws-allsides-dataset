import re
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from base import run_scraper
from bs4 import BeautifulSoup

DOMAIN = "washingtonexaminer.com"


def _extract_images(soup):
    images = []
    seen = set()

    article = soup.find("article", class_="fn-body")
    if not article:
        article = soup.find("article")

    if article:
        for fig in article.find_all("figure"):
            if fig.find_parent(class_=lambda c: c and "explore-more" in str(c)):
                continue
            img = fig.find("img")
            if not img:
                continue
            src = img.get("src", "") or img.get("data-src", "") or img.get("data-lazy-src", "")
            if not src or not src.startswith("http"):
                srcset = img.get("srcset", "")
                if srcset:
                    src = srcset.split(",")[0].strip().split(" ")[0]
            if not src or not src.startswith("http") or src in seen:
                continue
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
                img = data["image"]
                img_url = img.get("url", "") if isinstance(img, dict) else str(img)
                if img_url and img_url not in seen:
                    seen.add(img_url)
                    images.insert(0, {"url": img_url, "alt": ""})
        except Exception:
            pass

    if not images:
        og = soup.find("meta", property="og:image")
        if og:
            src = og.get("content", "")
            if src and src not in seen:
                images.append({"url": src, "alt": ""})

    return images


def _extract_videos(soup):
    videos = []
    seen = set()

    title_meta = soup.find("meta", property="og:video:title")
    url_meta = soup.find("meta", property="og:video")
    secure_url_meta = soup.find("meta", property="og:video:secure_url")
    width_meta = soup.find("meta", property="og:video:width")
    height_meta = soup.find("meta", property="og:video:height")

    vid_url = ""
    if secure_url_meta:
        vid_url = secure_url_meta.get("content", "")
    if not vid_url and url_meta:
        vid_url = url_meta.get("content", "")

    vid_title = title_meta["content"] if title_meta else ""
    vid_width = width_meta["content"] if width_meta else ""
    vid_height = height_meta["content"] if height_meta else ""

    for div in soup.find_all("div", id=re.compile(r"^Brid_\d+$")):
        vid_id = div["id"].replace("Brid_", "")
        if vid_id in seen:
            continue
        seen.add(vid_id)
        entry = {"type": "brid", "video_id": vid_id}
        if vid_title:
            entry["title"] = vid_title
        if vid_url:
            entry["url"] = vid_url
        if vid_width and vid_height:
            entry["width"] = int(vid_width)
            entry["height"] = int(vid_height)
        videos.append(entry)

    if not videos and vid_url:
        m = re.search(r'/(\d+)\.mp4', vid_url)
        vid_id = m.group(1) if m else ""
        entry = {"type": "brid"}
        if vid_id:
            entry["video_id"] = vid_id
        if vid_title:
            entry["title"] = vid_title
        if vid_url:
            entry["url"] = vid_url
        if vid_width and vid_height:
            entry["width"] = int(vid_width)
            entry["height"] = int(vid_height)
        videos.append(entry)

    return videos


def parse(html, url):
    soup = BeautifulSoup(html, "html.parser")

    h1 = soup.find("h1")
    headline = h1.get_text(strip=True) if h1 else ""

    images = _extract_images(soup)
    videos = _extract_videos(soup)

    article = soup.find("article", class_="fn-body")
    if not article:
        article = soup.find("article")

    if article:
        parts = []
        for el in article.find_all(["h2", "h3", "p"]):
            if el.find_parent(class_=lambda c: c and "explore-more" in str(c)):
                continue
            if el.name in ("h2", "h3"):
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

    paras = soup.select("article p, main p")
    body = "\n\n".join(p.get_text(" ", strip=True) for p in paras if len(p.get_text(strip=True)) > 20)
    return headline, body, images, videos, []


if __name__ == "__main__":
    run_scraper(DOMAIN, parse)
