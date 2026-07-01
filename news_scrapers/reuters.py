import sys, os, json, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from base import run_scraper
from bs4 import BeautifulSoup

DOMAIN = "reuters.com"


def _img_base(url):
    return url.split("?")[0] if url else ""


def _extract_images(soup):
    images = []
    seen = set()
    article = soup.find("article")
    scope = article if article else soup

    read_next = scope.find("div", attrs={"data-testid": "ReadNextV2"})

    for el in scope.descendants:
        if not hasattr(el, "get"):
            continue
        if read_next and el == read_next:
            break

        tid = el.get("data-testid", "")

        if re.match(r"gallery-\d+", tid):
            for slide in el.find_all("div", attrs={"data-testid": "CarouselSlide"}):
                img = slide.find("img")
                if img:
                    src = img.get("src", "")
                    base = _img_base(src)
                    if src and src.startswith("http") and base not in seen:
                        seen.add(base)
                        alt = img.get("alt", "")
                        images.append({"url": src, "alt": alt, "caption": alt})

        elif tid == "element":
            fig = el.find("figure")
            if fig:
                img = fig.find("img")
                if img:
                    src = img.get("src", "")
                    base = _img_base(src)
                    if src and src.startswith("http") and base not in seen:
                        seen.add(base)
                        alt = img.get("alt", "")
                        cap = fig.find("figcaption")
                        caption = cap.get_text(" ", strip=True) if cap else alt
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
                        base = _img_base(img_url)
                        if img_url and base not in seen:
                            seen.add(base)
                            images.append({"url": img_url, "alt": ""})
                elif isinstance(img_data, dict):
                    img_url = img_data.get("url", "")
                    base = _img_base(img_url)
                    if img_url and base not in seen:
                        seen.add(base)
                        images.insert(0, {"url": img_url, "alt": ""})
                elif isinstance(img_data, str) and _img_base(img_data) not in seen:
                    seen.add(_img_base(img_data))
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


def _extract_videos(soup):
    videos = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
            if isinstance(data, list):
                data = data[0]
            if isinstance(data, dict):
                vdata = data.get("video")
                if isinstance(vdata, dict):
                    videos.append({
                        "type": "video",
                        "title": vdata.get("name", ""),
                        "url": vdata.get("contentUrl", "") or vdata.get("embedUrl", ""),
                        "thumbnail": vdata.get("thumbnailUrl", ""),
                    })
                elif isinstance(vdata, list):
                    for v in vdata:
                        if isinstance(v, dict):
                            videos.append({
                                "type": "video",
                                "title": v.get("name", ""),
                                "url": v.get("contentUrl", "") or v.get("embedUrl", ""),
                                "thumbnail": v.get("thumbnailUrl", ""),
                            })
        except Exception:
            pass
    return videos


def _extract_interactives(soup):
    interactives = []
    seen = set()
    article = soup.find("article")
    scope = article if article else soup
    for iframe in scope.find_all("iframe"):
        src = iframe.get("src", "")
        if src and src not in seen:
            read_next = iframe.find_parent(attrs={"data-testid": "ReadNextV2"})
            if read_next:
                continue
            seen.add(src)
            interactives.append({"type": "iframe", "url": src})
    return interactives


def parse(html, url):
    soup = BeautifulSoup(html, "html.parser")

    h1 = soup.find("h1")
    headline = h1.get_text(strip=True) if h1 else ""

    images = _extract_images(soup)
    videos = _extract_videos(soup)
    interactives = _extract_interactives(soup)

    article = soup.find("article") or soup.find("main")
    if article:
        read_next = article.find("div", attrs={"data-testid": "ReadNextV2"})
        parts = []
        seen_texts = set()
        for el in article.descendants:
            if not hasattr(el, "get"):
                continue
            if read_next and el == read_next:
                break
            tid = el.get("data-testid", "")
            if re.match(r"paragraph-\d+", tid):
                text = el.get_text(" ", strip=True)
                if len(text) > 20 and text not in seen_texts:
                    seen_texts.add(text)
                    parts.append(text)
            elif el.name in ("h2", "h3"):
                text = el.get_text(strip=True)
                if text and len(text) > 3 and text not in seen_texts:
                    seen_texts.add(text)
                    parts.append(f"\n## {text}\n")
        if parts:
            return headline, "\n\n".join(parts), images, videos, interactives

    body_div = soup.find(class_=lambda c: c and "article-body" in str(c))
    if body_div:
        parts = []
        for p in body_div.find_all("p"):
            text = p.get_text(" ", strip=True)
            if len(text) >= 20:
                parts.append(text)
        body = "\n\n".join(parts)
        if body:
            return headline, body, images, videos, interactives

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
            if isinstance(data, list):
                data = data[0]
            if isinstance(data, dict) and data.get("articleBody"):
                return data.get("headline", headline), data["articleBody"], images, videos, interactives
        except Exception:
            pass

    paras = soup.select("article p, main p")
    body = "\n\n".join(p.get_text(" ", strip=True) for p in paras if len(p.get_text(strip=True)) > 20)
    return headline, body, images, videos, interactives


if __name__ == "__main__":
    run_scraper(DOMAIN, parse)
