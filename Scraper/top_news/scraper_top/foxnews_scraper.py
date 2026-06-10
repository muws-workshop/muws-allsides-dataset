"""
Fox News top-news scraper.
Collects headlines from the homepage and RSS feed, then fetches
each article page for full content (title, author, date, body, image).

Usage:
    conda activate scrap2
    python foxnews_scraper.py
"""
import json
import time
import os
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from curl_cffi import requests
from bs4 import BeautifulSoup

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "foxnews_top.jsonl")
MAX_RETRIES = 3
RETRY_DELAY = 3
REQUEST_DELAY = 1.0

RSS_URL = "https://moxie.foxnews.com/google-publisher/latest.xml"
HOMEPAGE_URL = "https://www.foxnews.com"


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
    """Scrape headlines from the Fox News homepage."""
    r = fetch(session, HOMEPAGE_URL)
    soup = BeautifulSoup(r.text, "html.parser")
    articles = []
    seen = set()

    main = soup.find("main", class_="main-content")
    container = main if main else soup

    for art in container.find_all("article"):
        title_el = art.find(["h2", "h3"], class_="title")
        if not title_el:
            continue
        a = title_el.find("a", href=True)
        if not a:
            continue
        url = a["href"]
        headline = a.get_text(strip=True)
        if not headline or url in seen:
            continue
        if not url.startswith("http"):
            url = HOMEPAGE_URL + url
        seen.add(url)

        img_el = art.find("img")
        image = ""
        if img_el:
            image = img_el.get("src", img_el.get("data-src", ""))
            if image.startswith("//"):
                image = "https:" + image

        articles.append({
            "url": url,
            "headline": headline,
            "image": image,
            "source_method": "homepage",
        })

    return articles


def collect_from_rss(session):
    """Collect latest articles from the Fox News RSS feed."""
    r = fetch(session, RSS_URL)
    soup = BeautifulSoup(r.text, "xml")
    articles = []
    seen = set()

    for item in soup.find_all("item"):
        link = item.find("link")
        title = item.find("title")
        pub_date = item.find("pubDate")
        if not link or not title:
            continue
        url = link.get_text(strip=True)
        if url in seen:
            continue
        seen.add(url)

        date_str = ""
        if pub_date:
            try:
                dt = parsedate_to_datetime(pub_date.get_text(strip=True))
                date_str = dt.strftime("%Y-%m-%dT%H:%M:%S%z")
            except Exception:
                date_str = pub_date.get_text(strip=True)

        articles.append({
            "url": url,
            "headline": title.get_text(strip=True),
            "date": date_str,
            "source_method": "rss",
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
    }
    try:
        r = fetch(session, url)
    except Exception:
        return result

    soup = BeautifulSoup(r.text, "html.parser")

    h1 = soup.find("h1", class_="headline")
    if not h1:
        h1 = soup.find("h1")
    if h1:
        result["title"] = h1.get_text(strip=True)

    h2 = soup.find("h2", class_="sub-headline")
    if h2:
        result["subtitle"] = h2.get_text(strip=True)

    author_el = soup.find("span", class_="author-byline")
    if not author_el:
        author_el = soup.find(class_=lambda c: c and "author" in str(c).lower())
    if author_el:
        raw = author_el.get_text(strip=True)
        raw = re.sub(r"^By\s*", "", raw).strip()
        raw = re.sub(r"Fox News$", "", raw).strip()
        raw = re.sub(r"OutKick$", "", raw).strip()
        result["author"] = raw

    time_el = soup.find("time")
    if time_el:
        result["date"] = time_el.get_text(strip=True)
        result["date_iso"] = time_el.get("datetime", "")

    body_div = soup.find("div", class_="article-body")
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

    breadcrumb = soup.find("a", class_="breadcrumb")
    if breadcrumb:
        result["category"] = breadcrumb.get_text(strip=True)

    ld_scripts = soup.find_all("script", type="application/ld+json")
    for s in ld_scripts:
        try:
            data = json.loads(s.string)
            if isinstance(data, list):
                data = data[0]
            if isinstance(data, dict) and "headline" in data:
                if not result["title"]:
                    result["title"] = data.get("headline", "")
                if not result["date_iso"]:
                    result["date_iso"] = data.get("datePublished", "")
                if not result["author"]:
                    author_d = data.get("author", "")
                    if isinstance(author_d, dict):
                        result["author"] = author_d.get("name", "")
                    elif isinstance(author_d, list):
                        result["author"] = ", ".join(a.get("name", "") for a in author_d if isinstance(a, dict))
        except Exception:
            pass

    return result


def run():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    session = make_session()

    print("Collecting Fox News headlines...")
    homepage_articles = collect_from_homepage(session)
    print(f"  Homepage: {len(homepage_articles)} articles")

    rss_articles = collect_from_rss(session)
    print(f"  RSS: {len(rss_articles)} articles")

    seen_urls = set()
    combined = []
    for art in homepage_articles + rss_articles:
        if art["url"] not in seen_urls:
            seen_urls.add(art["url"])
            combined.append(art)
    print(f"  Combined (deduplicated): {len(combined)} articles")

    results = []
    for i, art in enumerate(combined):
        print(f"  [{i+1}/{len(combined)}] Fetching: {art['headline'][:60]}...")
        details = parse_article_page(session, art["url"])
        record = {
            "source": "foxnews.com",
            "url": art["url"],
            "headline": details["title"] or art["headline"],
            "subtitle": details["subtitle"],
            "author": details["author"],
            "date": details["date"],
            "date_iso": details["date_iso"] or art.get("date", ""),
            "category": details["category"],
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
