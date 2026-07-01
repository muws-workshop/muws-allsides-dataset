import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from base import run_scraper
from bs4 import BeautifulSoup

DOMAIN = "politico.com"


def _extract_images(soup):
    images = []
    seen = set()

    # Politico puts the lead image caption in a separate <p data-testid="site-caption">
    # within the lead-box container, not inside the <figure>.
    lead_caption_el = soup.find("p", attrs={"data-testid": "site-caption"})
    lead_caption = lead_caption_el.get_text(" ", strip=True) if lead_caption_el else ""
    first_figure = True

    main = soup.find("main")
    scope = main if main else soup
    for fig in scope.find_all("figure"):
        img = fig.find("img")
        if img:
            src = img.get("src", "")
            if src and src.startswith("http") and src not in seen:
                seen.add(src)
                alt = img.get("alt", "")
                cap = fig.find("figcaption")
                caption = cap.get_text(" ", strip=True) if cap else ""
                if not caption and first_figure and lead_caption:
                    caption = lead_caption
                first_figure = False
                images.append({"url": src, "alt": alt, "caption": caption})

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
                    caption = lead_caption if (not images) else ""
                    images.insert(0, {"url": img_url, "alt": "", "caption": caption})
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
    main = soup.find("main")
    scope = main if main else soup
    for iframe in scope.find_all("iframe"):
        src = iframe.get("src", "")
        if src and src not in seen:
            seen.add(src)
            interactives.append({"type": "iframe", "url": src})
    return interactives


def parse(html, url):
    soup = BeautifulSoup(html, "html.parser")

    h1 = soup.select_one("h1")
    headline = h1.get_text(strip=True) if h1 else ""

    images = _extract_images(soup)
    videos = _extract_videos(soup)
    interactives = _extract_interactives(soup)

    # Politico uses two max-w-[630px] containers: a lead section and the main body
    content_divs = soup.find_all(
        "div", class_=lambda c: c and "max-w-[630px]" in str(c)
    )
    # Filter to those with actual paragraph content
    body_containers = [
        d for d in content_divs
        if len(d.find_all("p", recursive=False)) >= 2
    ]

    if body_containers:
        parts = []
        seen = set()
        for container in body_containers:
            for child in container.children:
                if not hasattr(child, "name") or not child.name:
                    continue
                if child.name in ("h2", "h3"):
                    text = child.get_text(strip=True)
                    if text and len(text) < 100 and text not in seen:
                        seen.add(text)
                        parts.append(f"\n## {text}\n")
                elif child.name == "p":
                    text = child.get_text(" ", strip=True)
                    if len(text) >= 20 and text not in seen:
                        seen.add(text)
                        parts.append(text)
        body = "\n\n".join(parts)
        if body:
            return headline, body, images, videos, interactives

    # Fallback: article or main <p> tags
    for selector in ["article p", "main p"]:
        paras = soup.select(selector)
        if paras:
            body = "\n\n".join(
                p.get_text(" ", strip=True) for p in paras
                if len(p.get_text(strip=True)) > 20
            )
            if body:
                return headline, body, images, videos, interactives

    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string)
            if isinstance(data, dict) and data.get("articleBody"):
                return data.get("headline", headline), data["articleBody"], images, videos, interactives
        except Exception:
            pass

    return headline, "", images, videos, interactives

if __name__ == "__main__":
    run_scraper(DOMAIN, parse)
