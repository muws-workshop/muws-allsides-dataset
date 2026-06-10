"""
NYTimes top-news scraper.
Uses RSS feeds (multiple sections) + homepage scraping for headlines,
then fetches each article page for full body text using curl_cffi
with a Google referer to bypass the paywall.

Usage:
    conda activate scrap2
    python nytimes_scraper.py
"""
import json
import time
import os
import re
import random
from datetime import datetime
from email.utils import parsedate_to_datetime
from curl_cffi import requests
from bs4 import BeautifulSoup

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "nytimes_top.jsonl")
MAX_RETRIES = 3
RETRY_DELAY = 4
REQUEST_DELAY = 3.0

HOMEPAGE_URL = "https://www.nytimes.com"
ARTICLE_HEADERS = {
    "Referer": "https://www.google.com/",
    "Accept-Language": "en-US,en;q=0.9",
}
RSS_FEEDS = [
    ("HomePage", "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml"),
    ("US", "https://rss.nytimes.com/services/xml/rss/nyt/US.xml"),
    ("World", "https://rss.nytimes.com/services/xml/rss/nyt/World.xml"),
    ("Politics", "https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml"),
    ("Business", "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml"),
    ("Technology", "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml"),
    ("Science", "https://rss.nytimes.com/services/xml/rss/nyt/Science.xml"),
    ("Health", "https://rss.nytimes.com/services/xml/rss/nyt/Health.xml"),
    ("Opinion", "https://rss.nytimes.com/services/xml/rss/nyt/Opinion.xml"),
]


def make_session():
    return requests.Session(impersonate="chrome")


def fetch(session, url, retries=MAX_RETRIES, headers=None):
    for attempt in range(retries):
        try:
            r = session.get(url, timeout=30, headers=headers or {})
            if r.status_code in (403, 429, 502):
                time.sleep(RETRY_DELAY * (attempt + 1) + random.uniform(0, 2))
                continue
            r.raise_for_status()
            return r
        except Exception:
            if attempt < retries - 1:
                time.sleep(RETRY_DELAY * (attempt + 1))
            else:
                raise
    return None


def collect_from_rss(session):
    """Collect articles from all NYT RSS feeds."""
    articles = []
    seen = set()

    for feed_name, feed_url in RSS_FEEDS:
        try:
            r = fetch(session, feed_url)
            if not r:
                raise RuntimeError("fetch returned None")
        except Exception:
            print(f"  Warning: failed to fetch RSS feed {feed_name}")
            continue

        soup = BeautifulSoup(r.text, "xml")
        count = 0

        for item in soup.find_all("item"):
            link_el = item.find("link")
            title_el = item.find("title")
            if not link_el or not title_el:
                continue

            url = link_el.get_text(strip=True)
            if url in seen:
                continue
            seen.add(url)
            count += 1

            headline = title_el.get_text(strip=True)

            date_iso = ""
            date_str = ""
            pub_date = item.find("pubDate")
            if pub_date:
                try:
                    dt = parsedate_to_datetime(pub_date.get_text(strip=True))
                    date_iso = dt.strftime("%Y-%m-%dT%H:%M:%S%z")
                    date_str = dt.strftime("%B %d, %Y %I:%M %p %Z")
                except Exception:
                    date_str = pub_date.get_text(strip=True)

            description = ""
            desc_el = item.find("description")
            if desc_el:
                description = desc_el.get_text(strip=True)

            author = ""
            creator = item.find("dc:creator") or item.find("creator")
            if creator:
                author = creator.get_text(strip=True)

            categories = []
            for cat in item.find_all("category"):
                cat_text = cat.get_text(strip=True)
                if cat_text:
                    categories.append(cat_text)

            image = ""
            media = item.find("media:content") or item.find("content")
            if media and media.get("url"):
                image = media["url"]

            image_credit = ""
            credit_el = item.find("media:credit") or item.find("credit")
            if credit_el:
                image_credit = credit_el.get_text(strip=True)

            articles.append({
                "url": url,
                "headline": headline,
                "description": description,
                "author": author,
                "date": date_str,
                "date_iso": date_iso,
                "categories": categories,
                "image": image,
                "image_credit": image_credit,
                "rss_section": feed_name,
                "source_method": "rss",
            })

        print(f"  RSS {feed_name}: {count} new articles")
        time.sleep(0.5)

    return articles


def collect_from_homepage(session):
    """Scrape headlines from the NYT homepage for articles not in RSS."""
    r = fetch(session, HOMEPAGE_URL)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    articles = []
    seen = set()

    for wrapper in soup.find_all(class_=lambda c: c and "story-wrapper" in str(c)):
        a = wrapper.find("a", href=True)
        if not a:
            continue

        href = a["href"]
        if href.startswith("/"):
            href = HOMEPAGE_URL + href

        if not re.search(r"/\d{4}/", href):
            continue
        if href in seen:
            continue
        seen.add(href)

        headline = a.get_text(strip=True)
        if not headline or len(headline) < 5:
            continue

        summary = ""
        p = wrapper.find("p")
        if p:
            p_text = p.get_text(strip=True)
            if p_text != headline and len(p_text) > 10:
                summary = p_text

        articles.append({
            "url": href,
            "headline": headline,
            "description": summary,
            "source_method": "homepage",
        })

    return articles


def parse_author(raw):
    """Clean NYT byline text: 'ByJohn SmithJohn Smith is a...' -> 'John Smith'."""
    raw = re.sub(r"^By\s*", "", raw).strip()
    parts = re.split(r"(?:Reporting from|covers|writes|is a |is an |is the )", raw, maxsplit=1)
    name = parts[0].strip()
    name = re.sub(r"and$", "", name).strip()
    return name


def parse_article_page(session, url):
    """Fetch a NYT article page and extract full content."""
    result = {
        "title": "",
        "subtitle": "",
        "author": "",
        "date_iso": "",
        "body": "",
        "image": "",
    }

    r = fetch(session, url, headers=ARTICLE_HEADERS)
    if not r:
        return result

    soup = BeautifulSoup(r.text, "html.parser")

    h1 = soup.find("h1")
    if h1:
        result["title"] = h1.get_text(strip=True)

    summary_el = soup.find("p", id=lambda i: i and "summary" in str(i))
    if not summary_el:
        summary_el = soup.find("p", class_=lambda c: c and "summary" in str(c).lower())
    if summary_el:
        result["subtitle"] = summary_el.get_text(strip=True)

    byline = soup.find(class_=lambda c: c and "byline" in str(c).lower())
    if byline:
        result["author"] = parse_author(byline.get_text(strip=True))

    time_el = soup.find("time")
    if time_el:
        result["date_iso"] = time_el.get("datetime", "")

    article = soup.find("article")
    if article:
        paragraphs = []
        for p in article.find_all("p"):
            text = p.get_text(strip=True)
            if len(text) > 30:
                paragraphs.append(text)
        result["body"] = "\n\n".join(paragraphs)

    meta_img = soup.find("meta", property="og:image")
    if meta_img:
        result["image"] = meta_img.get("content", "")

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
            if isinstance(data, list):
                data = data[0]
            if not isinstance(data, dict) or "headline" not in data:
                continue
            if not result["title"]:
                result["title"] = data.get("headline", "")
            if not result["date_iso"]:
                result["date_iso"] = data.get("datePublished", "")
            if not result["author"]:
                author_d = data.get("author", "")
                if isinstance(author_d, list):
                    result["author"] = ", ".join(
                        a.get("name", "") for a in author_d if isinstance(a, dict) and a.get("name")
                    )
                elif isinstance(author_d, dict):
                    result["author"] = author_d.get("name", "")
            body = data.get("articleBody", "")
            if body and not result["body"]:
                result["body"] = body
        except Exception:
            pass

    return result


def run():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    session = make_session()

    print("Collecting NYTimes headlines...")

    rss_articles = collect_from_rss(session)
    print(f"  Total RSS (deduplicated): {len(rss_articles)} articles")

    homepage_articles = collect_from_homepage(session)
    print(f"  Homepage: {len(homepage_articles)} articles")

    rss_urls = {a["url"] for a in rss_articles}
    homepage_only = [a for a in homepage_articles if a["url"] not in rss_urls]
    print(f"  Homepage-only (not in RSS): {len(homepage_only)} articles")

    all_articles = []
    for art in rss_articles:
        all_articles.append(art)
    for art in homepage_only:
        all_articles.append({
            "url": art["url"],
            "headline": art["headline"],
            "description": art.get("description", ""),
            "author": "",
            "date": "",
            "date_iso": "",
            "categories": [],
            "image": "",
            "image_credit": "",
            "rss_section": "",
            "source_method": "homepage",
        })

    print(f"\nFetching {len(all_articles)} article pages...")
    results = []
    success = 0
    for i, art in enumerate(all_articles):
        print(f"  [{i+1}/{len(all_articles)}] {art['headline'][:60]}...", end=" ")

        details = parse_article_page(session, art["url"])

        record = {
            "source": "nytimes.com",
            "url": art["url"],
            "headline": details["title"] or art["headline"],
            "description": art.get("description", "") or details.get("subtitle", ""),
            "author": details["author"] or art.get("author", ""),
            "date": art.get("date", ""),
            "date_iso": details["date_iso"] or art.get("date_iso", ""),
            "categories": art.get("categories", []),
            "body": details["body"],
            "image": details["image"] or art.get("image", ""),
            "image_credit": art.get("image_credit", ""),
            "rss_section": art.get("rss_section", ""),
            "source_method": art["source_method"],
            "scraped_at": datetime.utcnow().isoformat() + "Z",
        }
        results.append(record)

        if details["body"]:
            success += 1
            print("OK")
        else:
            print("(no body)")

        time.sleep(REQUEST_DELAY + random.uniform(0, 2))

    with open(OUTPUT_FILE, "w") as f:
        for rec in results:
            json.dump(rec, f)
            f.write("\n")

    with_body = sum(1 for r in results if r["body"])
    with_author = sum(1 for r in results if r["author"])
    print(f"\nDone. Saved {len(results)} articles to {OUTPUT_FILE}")
    print(f"  With full body text: {with_body}/{len(results)}")
    print(f"  With author: {with_author}/{len(results)}")


if __name__ == "__main__":
    run()
