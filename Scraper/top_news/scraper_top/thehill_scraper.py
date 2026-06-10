"""
The Hill top-news scraper.
Collects headlines from the homepage, then fetches each article page
for full content (title, author, date, body, image).

Usage:
    conda activate scrap2
    python thehill_scraper.py
"""
import json
import time
import os
import re
from datetime import datetime
from curl_cffi import requests
from bs4 import BeautifulSoup

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "thehill_top.jsonl")
MAX_RETRIES = 3
RETRY_DELAY = 3
REQUEST_DELAY = 1.0

HOMEPAGE_URL = "https://thehill.com"


def make_session():
    return requests.Session(impersonate="chrome")


def fetch(session, url, retries=MAX_RETRIES):
    for attempt in range(retries):
        try:
            r = session.get(url, timeout=30)
            if r.status_code == 502:
                time.sleep(RETRY_DELAY * (attempt + 1))
                continue
            r.raise_for_status()
            return r
        except Exception:
            if attempt < retries - 1:
                time.sleep(RETRY_DELAY * (attempt + 1))
            else:
                raise
    raise RuntimeError(f"Failed after {retries} retries: {url}")


def collect_from_homepage(session):
    """Scrape headlines from The Hill homepage."""
    r = fetch(session, HOMEPAGE_URL)
    soup = BeautifulSoup(r.text, "html.parser")
    articles = []
    seen = set()

    for art in soup.find_all("article"):
        links = art.find_all("a", href=True)
        url = ""
        headline = ""

        skip_patterns = ["/author/", "/newsletters/", "/category/"]
        for a in links:
            href = a["href"]
            text = a.get_text(strip=True)
            if "thehill.com/" in href and not any(p in href for p in skip_patterns):
                if not url:
                    url = href
                if text and not headline and len(text) > 15:
                    headline = text

        if not url or url in seen:
            continue

        h_el = art.find(["h1", "h2"])
        if h_el:
            h_text = h_el.get_text(strip=True)
            if h_text and len(h_text) > len(headline):
                headline = re.sub(r'\d{1,2}:\d{2}$', '', h_text).strip()

        if not headline:
            continue

        seen.add(url)

        img_el = art.find("img")
        image = img_el.get("src", "") if img_el else ""

        category = ""
        cat_link = art.find("a", href=lambda h: h and "/homenews/" in h or "/policy/" in h or "/opinion/" in h)
        if cat_link:
            category = cat_link.get_text(strip=True)

        articles.append({
            "url": url,
            "headline": headline,
            "image": image,
            "category": category,
            "source_method": "homepage",
        })

    # Also collect from non-article link sections (featured cards, etc.)
    for section_cls in ["featured-cards", "top-stories", "latest-news"]:
        section = soup.find(class_=lambda c: c and section_cls in str(c))
        if not section:
            continue
        for a in section.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(strip=True)
            if "thehill.com/" in href and href not in seen and text and len(text) > 10:
                if "/author/" in href or "/newsletters/" in href:
                    continue
                seen.add(href)
                articles.append({
                    "url": href,
                    "headline": text[:200],
                    "image": "",
                    "category": "",
                    "source_method": "homepage_section",
                })

    return articles


def parse_article_page(session, url):
    """Fetch an article page and extract structured content."""
    result = {
        "title": "",
        "subtitle": "",
        "author": "",
        "date": "",
        "date_iso": "",
        "body": "",
        "image": "",
        "category": "",
        "tags": [],
    }
    try:
        r = fetch(session, url)
    except Exception:
        return result

    soup = BeautifulSoup(r.text, "html.parser")

    h1 = soup.find("h1", class_="page-title")
    if not h1:
        h1 = soup.find("h1")
    if h1:
        result["title"] = h1.get_text(strip=True)

    subtitle_el = soup.find(class_=lambda c: c and "subtitle" in str(c).lower())
    if subtitle_el:
        sub_text = subtitle_el.get_text(strip=True)
        if "one-click link to sign in" not in sub_text and "sign in" not in sub_text.lower():
            result["subtitle"] = sub_text

    body_div = soup.find(class_="article__text")
    if body_div:
        paragraphs = []
        for p in body_div.find_all("p"):
            text = p.get_text(strip=True)
            if text and len(text) > 10:
                paragraphs.append(text)
        result["body"] = "\n\n".join(paragraphs)

    meta_img = soup.find("meta", property="og:image")
    if meta_img:
        result["image"] = meta_img.get("content", "")

    tag_els = soup.find_all("a", href=lambda h: h and "/tag/" in h)
    result["tags"] = [t.get_text(strip=True) for t in tag_els if t.get_text(strip=True)]

    ld_scripts = soup.find_all("script", type="application/ld+json")
    for s in ld_scripts:
        try:
            data = json.loads(s.string)
            if isinstance(data, list):
                data = data[0]
            if not isinstance(data, dict) or "headline" not in data:
                continue
            if not result["title"]:
                result["title"] = data.get("headline", "")
            result["date_iso"] = data.get("datePublished", "")
            if result["date_iso"]:
                try:
                    dt = datetime.fromisoformat(result["date_iso"])
                    result["date"] = dt.strftime("%B %d, %Y %I:%M %p")
                except Exception:
                    result["date"] = result["date_iso"]
            author_d = data.get("author", "")
            if isinstance(author_d, dict):
                result["author"] = author_d.get("name", "")
            elif isinstance(author_d, list):
                result["author"] = ", ".join(
                    a.get("name", "") for a in author_d if isinstance(a, dict) and a.get("name")
                )
            if not result["category"]:
                section = data.get("articleSection", "")
                if isinstance(section, list):
                    result["category"] = ", ".join(section)
                elif section:
                    result["category"] = section
        except Exception:
            pass

    return result


def run():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    session = make_session()

    print("Collecting The Hill headlines...")
    articles = collect_from_homepage(session)
    print(f"  Homepage: {len(articles)} articles")

    results = []
    for i, art in enumerate(articles):
        print(f"  [{i+1}/{len(articles)}] Fetching: {art['headline'][:60]}...")
        details = parse_article_page(session, art["url"])
        record = {
            "source": "thehill.com",
            "url": art["url"],
            "headline": details["title"] or art["headline"],
            "subtitle": details["subtitle"],
            "author": details["author"],
            "date": details["date"],
            "date_iso": details["date_iso"],
            "category": details["category"] or art.get("category", ""),
            "tags": details["tags"],
            "body": details["body"],
            "image": details["image"] or art.get("image", ""),
            "source_method": art["source_method"],
            "scraped_at": datetime.utcnow().isoformat() + "Z",
        }
        results.append(record)
        time.sleep(REQUEST_DELAY)

    with open(OUTPUT_FILE, "w") as f:
        for rec in results:
            json.dump(rec, f)
            f.write("\n")

    print(f"\nDone. Saved {len(results)} articles to {OUTPUT_FILE}")
    with_body = sum(1 for r in results if r["body"])
    print(f"  With body text: {with_body}/{len(results)}")


if __name__ == "__main__":
    run()
