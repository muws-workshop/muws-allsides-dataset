"""
AllSides full scraper: June 2025 - May 2026 (multi-threaded).
Uses curl_cffi to bypass Cloudflare + BeautifulSoup for parsing.
Output matches the MUWS dataset schema.

Writes results incrementally so you can inspect progress with:
    python inspect_run.py
"""
import json
import time
import re
import os
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock, Semaphore
from curl_cffi import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

BASE = "https://www.allsides.com"
MAX_RETRIES = 4
RETRY_DELAY = 4
WORKERS = 8
REQUEST_DELAY = 0.5

DATE_START = "2026-01-01"
DATE_END = "2026-01-31"
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
OUTPUT_FILE_NAME = f"allsides_{DATE_START}_{DATE_END}.jsonl"
MAX_LISTING_PAGES = 100

write_lock = Lock()
request_semaphore = Semaphore(WORKERS)
stats = {"completed": 0, "errors": 0, "no_featured": 0}
stats_lock = Lock()

TAG_PREFIXES = [
    ("Fact Check", "FACT CHECK"),
    ("Analysis", "ANALYSIS"),
    ("Opinion", "OPINION"),
    ("News", "NEWS"),
]
OPEN_ON_RE = re.compile(r'(Open on .+?)(?:Possible Paywall)?$')


def make_session():
    return requests.Session(impersonate="chrome")


def fetch(session, url: str) -> BeautifulSoup:
    for attempt in range(MAX_RETRIES):
        try:
            with request_semaphore:
                r = session.get(url, timeout=30)
                time.sleep(REQUEST_DELAY)
            if r.status_code == 502:
                time.sleep(RETRY_DELAY * (attempt + 1))
                continue
            r.raise_for_status()
            return BeautifulSoup(r.text, "html.parser")
        except Exception:
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY * (attempt + 1))
            else:
                raise
    raise RuntimeError(f"Failed after {MAX_RETRIES} retries: {url}")


def parse_date(raw: str) -> str:
    cleaned = re.sub(r'^.*?•\s*', '', raw)
    cleaned = re.sub(r'(\d+)(st|nd|rd|th)', r'\1', cleaned).strip()
    try:
        return datetime.strptime(cleaned, "%B %d, %Y").strftime("%Y-%m-%d")
    except ValueError:
        return ""


def parse_bias(img_src: str) -> str:
    if not img_src:
        return "unknown"
    name = img_src.rsplit("/", 1)[-1].lower()
    if "leaning-left" in name:
        return "lean left"
    if "leaning-right" in name:
        return "lean right"
    if "bias-left" in name:
        return "left"
    if "bias-right" in name:
        return "right"
    if "center" in name:
        return "center"
    return "unknown"


def parse_article_from_item(item) -> dict:
    headline_el = item.find("div", class_=lambda c: c and "leading-tight" in c)
    headline = headline_el.get_text(strip=True) if headline_el else ""
    link_el = item.find("a", href=lambda h: h and "/news/" in h)
    allsides_path = link_el["href"] if link_el else ""
    allsides_link = (BASE + allsides_path) if allsides_path.startswith("/") else allsides_path
    source_el = item.find("p", class_=lambda c: c and "news-source" in c)
    source = source_el.get_text(strip=True) if source_el else ""
    bias_img = item.find("img", alt=lambda a: a and "Bias" in str(a))
    rating_img = bias_img["src"] if bias_img else ""
    rating = parse_bias(rating_img)
    news_type_el = item.find(class_=lambda c: c and "news-type" in str(c))
    news_type = news_type_el.get_text(strip=True) if news_type_el else ""
    return {
        "headline": headline, "source": source, "allsides_link": allsides_link,
        "rating_img": rating_img, "rating": rating, "news_type": news_type,
    }


def parse_featured_from_container(soup) -> dict:
    featured = {}
    container = soup.find("div", class_=lambda c: c and "gap-5" in c and "mb-8" in c)
    if not container:
        return featured

    for child in container.children:
        if not (hasattr(child, "name") and child.name == "div"):
            continue
        if not child.find("div", class_=lambda c: c and "global-bias-label" in c):
            continue
        stance_cls = [c for c in child.get("class", []) if c in ("left", "center", "right")]
        if not stance_cls:
            continue
        stance = stance_cls[0]
        if stance in featured:
            continue

        headline = ""
        ext_link = ""
        source = ""
        rating_img_url = ""
        image_link = ""
        summary = ""

        for a in child.find_all("a"):
            href = a.get("href", "")
            text = a.get_text(strip=True)
            if "/news-source/" in href:
                if text and text != "See rating details":
                    source = text
            elif text.startswith("Open on "):
                if not ext_link:
                    ext_link = href
            elif text and not headline:
                headline = text
                ext_link = href

        img = child.find("img", alt=lambda a: a and "Bias" in str(a))
        if img:
            rating_img_url = img.get("src", "")

        article_img = child.find("img", alt=lambda a: a and "Bias" not in str(a))
        if article_img:
            image_link = article_img.get("src", "")

        for cd in child.find_all("div", class_=lambda c: c and "mt-4" in c):
            text = cd.get_text(strip=True)
            if text and not text.startswith("Open on") and len(text) > 10:
                summary = text
                break

        featured[stance] = {
            "source": source, "headline": headline, "link": ext_link,
            "rating_img": rating_img_url, "rating": parse_bias(rating_img_url),
            "summary": summary, "image_link": image_link, "news_type": "",
        }

    return featured


def fetch_article_details(session, allsides_link: str) -> dict:
    result = {"link": "", "summary": "", "content": "", "image_link": ""}
    if not allsides_link:
        return result
    try:
        soup = fetch(session, allsides_link)
        ext = soup.find("a", string=lambda t: t and "Read Full Story" in str(t))
        if ext:
            result["link"] = ext.get("href", "")
        body = soup.find("div", class_="body")
        text = body.get_text(strip=True) if body else ""
        result["summary"] = text
        result["content"] = text
        pic = soup.find("picture")
        if pic:
            source = pic.find("source")
            if source:
                result["image_link"] = source.get("srcset", "")
    except Exception:
        pass
    return result


# ── Phase 1: Collect all story URLs ─────────────────────────────────────

def collect_all_story_urls():
    session = make_session()
    all_stories = []
    seen = set()

    pbar = tqdm(range(1, MAX_LISTING_PAGES + 1), desc="Phase 1: Listing pages", unit="pg")
    for page in pbar:
        try:
            soup = fetch(session, f"{BASE}/recent-headline-roundups?page={page}")
        except Exception:
            break
        cards = soup.find_all("div", class_=lambda c: c and "clearfix" in c and "border-b" in c)
        if not cards:
            break
        new_count = 0
        for card in cards:
            h2 = card.find("h2")
            link_el = h2.find("a") if h2 else None
            if not link_el:
                continue
            href = link_el.get("href", "")
            if href in seen:
                continue
            seen.add(href)
            new_count += 1
            headline = link_el.get_text(strip=True)
            headline_link = BASE + href if href.startswith("/") else href
            all_stories.append({"headline": headline, "headline_link": headline_link})
        pbar.set_postfix(total=len(all_stories), new=new_count)
        if new_count == 0:
            break
        time.sleep(0.3)
    return all_stories


# ── Process one story end-to-end and write immediately ────────────────

def process_and_write(story, output_path):
    session = make_session()

    # --- story detail page ---
    try:
        soup = fetch(session, story["headline_link"])
    except Exception:
        with stats_lock:
            stats["errors"] += 1
        return None

    date_el = soup.find("p", class_=lambda c: c and "tracking-wide" in c)
    if not date_el:
        date_el = soup.find("p", class_=lambda c: c and "text-gray-500" in c)
    story["date"] = parse_date(date_el.get_text(strip=True)) if date_el else ""

    if not story["date"] or story["date"] < DATE_START or story["date"] > DATE_END:
        return None

    topic_el = soup.find("a", href=lambda h: h and "/topics/" in h)
    if topic_el:
        story["topic"] = topic_el.get_text(strip=True).replace("News and Information about ", "")
        story["topic_link"] = BASE + topic_el["href"]
    else:
        story["topic"] = ""
        story["topic_link"] = ""

    tag_links = soup.find_all("a", href=lambda h: h and "/tags/" in h)
    story["tags"] = [t.get_text(strip=True).rstrip(",").strip() for t in tag_links]

    desc = soup.find("div", class_=lambda c: c and "story-id-page-description" in c)
    if desc:
        story["summary"] = desc.get_text(strip=True)
    else:
        meta = soup.find("meta", attrs={"name": "description"})
        story["summary"] = meta.get("content", "") if meta else ""

    featured = parse_featured_from_container(soup)

    items = soup.find_all("div", class_=lambda c: c and "news-item" in c)
    more_articles = {"left": [], "center": [], "right": []}
    seen_headlines = set()
    for stance in featured:
        if featured[stance]["headline"]:
            seen_headlines.add(featured[stance]["headline"])
    for item in items:
        classes = item.get("class", [])
        for stance in ["left", "center", "right"]:
            if stance in classes:
                art = parse_article_from_item(item)
                if art["headline"] and art["headline"] not in seen_headlines:
                    seen_headlines.add(art["headline"])
                    more_articles[stance].append(art)
                break

    for stance in ["left", "center", "right"]:
        if stance not in featured and more_articles[stance]:
            first = more_articles[stance].pop(0)
            featured[stance] = {
                "source": first["source"], "headline": first["headline"],
                "link": "", "rating_img": first["rating_img"],
                "rating": first["rating"], "summary": "", "image_link": "",
                "news_type": first.get("news_type", ""),
                "_needs_fetch": True, "_allsides_link": first["allsides_link"],
            }

    had_featured = len(featured) > 0

    # --- fetch article details ---
    for stance in ["left", "center", "right"]:
        if stance in featured:
            feat = featured[stance]
            if feat.get("_needs_fetch"):
                details = fetch_article_details(session, feat["_allsides_link"])
                feat["link"] = details["link"]
                feat["summary"] = details["summary"]
                feat["image_link"] = details["image_link"]
                feat.pop("_needs_fetch", None)
                feat.pop("_allsides_link", None)
            story[stance] = feat
        else:
            story[stance] = ""

        more = []
        for art_raw in more_articles.get(stance, []):
            details = fetch_article_details(session, art_raw["allsides_link"])
            more.append({
                "source": art_raw["source"], "headline": art_raw["headline"],
                "link": details["link"], "rating_img": art_raw["rating_img"],
                "rating": art_raw["rating"], "image_link": details["image_link"],
                "news_type": art_raw.get("news_type", ""),
                "allsides_link": art_raw["allsides_link"], "content": details["content"],
            })
        story[f"more_{stance}"] = more

    # --- write immediately ---
    with write_lock:
        with open(output_path, "a") as f:
            json.dump(story, f)
            f.write("\n")

    with stats_lock:
        stats["completed"] += 1
        if not had_featured:
            stats["no_featured"] += 1

    return story


# ── Main ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output_path = os.path.join(OUTPUT_DIR, OUTPUT_FILE_NAME)
    start_time = time.time()

    print(f"\n{'='*60}")
    print(f"AllSides Scraper: {DATE_START} to {DATE_END}")
    print(f"Output: {output_path}")
    print(f"Workers: {WORKERS} threads, {REQUEST_DELAY}s delay per request")
    print(f"{'='*60}\n")

    # Phase 1: listing pages
    stories = collect_all_story_urls()
    t1 = time.time() - start_time
    print(f"\nFound {len(stories)} unique stories. ({t1:.0f}s)\n")

    # Clear output file
    with open(output_path, "w") as f:
        pass

    # Phase 2+3 combined: process each story end-to-end, write incrementally
    errors = 0
    kept = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = {
            executor.submit(process_and_write, story, output_path): story
            for story in stories
        }
        pbar = tqdm(as_completed(futures), total=len(futures),
                    desc=f"Processing stories ({WORKERS} threads)", unit="story")
        for future in pbar:
            try:
                result = future.result()
                if result is not None:
                    kept += 1
            except Exception:
                errors += 1
            with stats_lock:
                pbar.set_postfix(
                    kept=stats["completed"],
                    no_feat=stats["no_featured"],
                    errors=stats["errors"],
                )

    t2 = time.time() - start_time

    # Sort the output file by date
    with open(output_path) as f:
        all_records = [json.loads(line) for line in f if line.strip()]
    all_records.sort(key=lambda s: s.get("date", ""), reverse=True)

    # Clean featured summaries: strip tag prefixes and "Open on" suffixes
    clean_stats = {"tag_extracted": 0, "open_on_extracted": 0}
    for rec in all_records:
        for stance in ["left", "center", "right"]:
            entry = rec.get(stance)
            if not isinstance(entry, dict) or not entry.get("summary"):
                continue
            summary = entry["summary"]
            news_type = entry.get("news_type", "")
            open_on_source = ""

            m = OPEN_ON_RE.search(summary)
            if m:
                open_on_source = m.group(1).replace("Open on ", "", 1)
                summary = summary[:m.start()].rstrip()
                clean_stats["open_on_extracted"] += 1

            for prefix, nt_value in TAG_PREFIXES:
                if summary.startswith(prefix) and len(summary) > len(prefix):
                    next_char = summary[len(prefix)]
                    if next_char.isupper() or next_char == ' ':
                        summary = summary[len(prefix):].lstrip()
                        if not news_type:
                            news_type = nt_value
                        clean_stats["tag_extracted"] += 1
                        break

            entry["summary"] = summary
            if news_type:
                entry["news_type"] = news_type
            if open_on_source:
                entry["open_on_source"] = open_on_source

    with open(output_path, "w") as f:
        for rec in all_records:
            json.dump(rec, f)
            f.write("\n")

    dates = sorted(r["date"] for r in all_records if r.get("date"))
    print(f"\n{'='*60}")
    print(f"DONE in {t2/60:.1f} minutes")
    print(f"Saved {len(all_records)} stories to {output_path}")
    print(f"  No featured articles (used fallback): {stats['no_featured']}")
    print(f"  Errors: {stats['errors']}")
    print(f"  Cleaned: {clean_stats['tag_extracted']} tag prefixes, {clean_stats['open_on_extracted']} 'Open on' suffixes")
    if dates:
        print(f"  Date range: {dates[0]} to {dates[-1]}")
    print(f"{'='*60}")
