import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from base import run_scraper
from bs4 import BeautifulSoup

DOMAIN = "apnews.com"


def _best_src(picture):
    for source in picture.find_all("source"):
        srcset = source.get("srcset", "") or source.get("data-flickity-lazyload-srcset", "")
        stype = source.get("type", "")
        if srcset and srcset.startswith("http") and "webp" not in stype:
            return srcset.split(",")[0].split()[0]
    img = picture.find("img")
    if img:
        for attr in ("src", "data-flickity-lazyload"):
            src = img.get(attr, "")
            if src and src.startswith("http"):
                return src
    return ""


def _extract_carousel(soup):
    images = []
    videos = []
    seen_imgs = set()
    seen_vids = set()

    carousel = soup.find(class_="Carousel-slides")
    if not carousel:
        return images, videos

    for slide in carousel.find_all(class_="Carousel-slide", recursive=False):
        cs = slide.find(class_="CarouselSlide")
        if not cs:
            continue

        media = cs.find(class_=lambda c: c and "CarouselSlide-media" in str(c))
        if not media:
            continue
        classes = " ".join(media.get("class", []))

        info = cs.find(class_="CarouselSlide-info-content")
        caption = ""
        if info:
            desc = info.find(class_="CarouselSlide-infoDescription")
            if desc:
                caption = desc.get_text(strip=True)

        if "videoSlide" in classes:
            jw = media.find("bsp-jw-player")
            if not jw:
                jw_div = media.find(class_="JWVideoPlayer")
                media_id = jw_div.get("data-jw-media_id", "") if jw_div else ""
            else:
                media_id = jw.get("data-media-id", "")
            thumbnail = ""
            link = media.find("link", rel="preload")
            if link:
                thumbnail = link.get("href", "")
            if media_id and media_id not in seen_vids:
                seen_vids.add(media_id)
                videos.append({"media_id": media_id, "thumbnail": thumbnail, "caption": caption})

        elif "imageSlide" in classes:
            pic = media.find("picture")
            if pic:
                src = _best_src(pic)
                if src and src not in seen_imgs:
                    seen_imgs.add(src)
                    img = pic.find("img")
                    alt = img.get("alt", "") if img else ""
                    images.append({"url": src, "alt": alt, "caption": caption})

    return images, videos


def _extract_images(soup):
    images = []
    seen = set()

    for container in [soup.find(class_="Page-lead"),
                      soup.find(class_=lambda c: c and "RichTextStoryBody" in str(c))]:
        if not container:
            continue
        for pic in container.find_all("picture"):
            skip = False
            for ancestor in pic.parents:
                classes = " ".join(ancestor.get("class", []))
                if any(kw in classes for kw in ("PagePromo", "PageList", "Carousel")):
                    skip = True
                    break
            if skip:
                continue
            src = _best_src(pic)
            if not src or src in seen:
                continue
            seen.add(src)
            img = pic.find("img")
            alt = img.get("alt", "") if img else ""
            images.append({"url": src, "alt": alt})

    return images


def _extract_videos(soup):
    videos = []
    seen = set()
    body_div = soup.find(class_=lambda c: c and "RichTextStoryBody" in str(c))
    if not body_div:
        return videos
    for jw in body_div.find_all("bsp-jw-player"):
        if any("Carousel" in " ".join(a.get("class", [])) for a in jw.parents if hasattr(a, "get")):
            continue
        media_id = jw.get("data-media-id", "")
        if media_id and media_id not in seen:
            seen.add(media_id)
            thumbnail = ""
            link = jw.find_previous("link", rel="preload")
            if link:
                href = link.get("href", "")
                if href and "dims.apnews.com" in href:
                    thumbnail = href
            videos.append({"media_id": media_id, "thumbnail": thumbnail, "caption": ""})
    return videos


def _extract_interactives(soup):
    interactives = []
    seen = set()
    body_div = soup.find(class_=lambda c: c and "RichTextStoryBody" in str(c))
    if not body_div:
        return interactives
    for div in body_div.find_all("div", class_=lambda c: c and "HTMLModuleEnhancement" in str(c)):
        for iframe in div.find_all("iframe"):
            src = iframe.get("src", "")
            title = iframe.get("title", "")
            if src and src.startswith("http") and src not in seen:
                seen.add(src)
                interactives.append({"url": src, "title": title})
    return interactives


def parse(html, url):
    soup = BeautifulSoup(html, "html.parser")

    h1 = soup.select_one("h1")
    headline = h1.get_text(strip=True) if h1 else ""

    carousel_images, carousel_videos = _extract_carousel(soup)
    body_images = _extract_images(soup)
    body_videos = _extract_videos(soup)
    interactives = _extract_interactives(soup)

    images = carousel_images + body_images
    videos = carousel_videos + body_videos

    body_div = soup.find(class_=lambda c: c and "RichTextStoryBody" in str(c))
    if body_div:
        parts = []
        for child in body_div.children:
            if not hasattr(child, "name") or not child.name:
                continue
            if child.name == "h2":
                text = child.get_text(strip=True)
                if text:
                    parts.append(f"\n## {text}\n")
            elif child.name == "p":
                text = child.get_text(" ", strip=True)
                if len(text) >= 20:
                    parts.append(text)
        body = "\n\n".join(parts)
        if body:
            return headline, body, images, videos, interactives

    paras = soup.select("article p")
    body = "\n\n".join(p.get_text(" ", strip=True) for p in paras if len(p.get_text(strip=True)) > 20)
    if body:
        return headline, body, images, videos, interactives

    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string)
            if isinstance(data, dict) and data.get("articleBody"):
                return data.get("headline", headline), data["articleBody"], images, videos, interactives
        except Exception:
            pass

    return headline, body, images, videos, interactives


if __name__ == "__main__":
    run_scraper(DOMAIN, parse)
