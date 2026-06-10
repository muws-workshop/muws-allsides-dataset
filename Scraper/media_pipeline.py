"""
AllSides Featured-Article Scraper — unified pipeline.

Scrapes full article text for the top-tier featured Left/Center/Right links
from an AllSides dataset. Custom parsers for the 9 highest-volume domains.

Usage:
    python media_pipeline.py --mode scrape
    python media_pipeline.py --mode scrape --domain foxnews.com --limit 5 --debug
    python media_pipeline.py --mode scrape --stance right
    python media_pipeline.py --mode patch --domain nytimes.com
    python media_pipeline.py --mode audit
"""

import argparse
import hashlib
import json
import os
import random
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

from curl_cffi import requests
from bs4 import BeautifulSoup
import trafilatura

# ── Paths ────────────────────────────────────────────────────────────────

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_INPUT = os.path.join(SCRIPT_DIR, "output_2025_2026", "allsides_jan2025_may2026_combined.jsonl")
DEFAULT_OUTPUT = os.path.join(SCRIPT_DIR, "crawled_articles_corpus.json")

# ── Target domains ───────────────────────────────────────────────────────

TARGET_DOMAINS = frozenset([
    "nytimes.com", "apnews.com", "cnn.com",
    "thehill.com", "newsweek.com", "reuters.com",
    "foxnews.com", "nypost.com", "washingtonexaminer.com",
])

# ── Network constants ────────────────────────────────────────────────────

REQUEST_TIMEOUT = 20
DELAY_RANGE = (1.5, 4.0)
MAX_RETRIES = 3
RETRY_BACKOFF = 3

CHROME_PROFILES = ["chrome", "chrome110", "chrome120"]

CLOUDFLARE_MARKERS = [
    "cf-browser-verification",
    "cf_chl_opt",
    "Checking your browser",
    "challenges.cloudflare.com",
]

FLUSH_INTERVAL = 25

# ═════════════════════════════════════════════════════════════════════════
# INPUT LOADER
# ═════════════════════════════════════════════════════════════════════════


def derive_story_id(record):
    hl = record.get("headline_link", "")
    if "/story/" in hl:
        return hl.split("/story/")[-1].strip("/")
    date = record.get("date", "")
    headline = record.get("headline", "")
    return hashlib.sha1(f"{date}|{headline}".encode()).hexdigest()[:16]


def normalize_domain(url):
    netloc = urlparse(url).netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


def load_input(input_path):
    """Read JSONL, return {domain: [work_items]} for in-scope domains only."""
    buckets = {d: [] for d in TARGET_DOMAINS}
    stories_seen = 0

    with open(input_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            story_id = derive_story_id(record)
            stories_seen += 1

            for slot in ("left", "center", "right"):
                val = record.get(slot)
                if not isinstance(val, dict):
                    continue
                link = val.get("link", "")
                if not link:
                    continue
                domain = normalize_domain(link)
                if domain not in TARGET_DOMAINS:
                    continue
                buckets[domain].append({
                    "story_id": story_id,
                    "slot": slot,
                    "url": link,
                    "source": val.get("source", ""),
                    "rating": val.get("rating", ""),
                    "domain": domain,
                })

    total = sum(len(v) for v in buckets.values())
    print(f"Loaded {stories_seen} stories, {total} in-scope article slots")
    for d in sorted(buckets, key=lambda d: len(buckets[d]), reverse=True):
        if buckets[d]:
            print(f"  {d}: {len(buckets[d])}")
    return buckets


# ═════════════════════════════════════════════════════════════════════════
# STATE / PERSISTENCE
# ═════════════════════════════════════════════════════════════════════════


def load_master(output_path):
    if os.path.exists(output_path):
        with open(output_path) as f:
            return json.load(f)
    return {}


def save_master(master, output_path):
    dir_name = os.path.dirname(output_path) or "."
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(master, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, output_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def is_done(master, story_id, slot):
    story = master.get(story_id)
    if not story:
        return False
    entry = story.get(slot)
    if not entry:
        return False
    return entry.get("execution_status") == "SUCCESS"


def write_result(master, story_id, slot, result):
    if story_id not in master:
        master[story_id] = {}
    master[story_id][slot] = result


# ═════════════════════════════════════════════════════════════════════════
# NETWORK LAYER
# ═════════════════════════════════════════════════════════════════════════


def make_session():
    return requests.Session(impersonate=random.choice(CHROME_PROFILES))


def classify_response(resp, html):
    if resp.status_code in (401, 403):
        return "FAILED_PAYWALL"
    if resp.status_code == 429:
        return "FAILED_PAYWALL"
    if resp.status_code >= 400:
        return f"FAILED_HTTP_{resp.status_code}"
    for marker in CLOUDFLARE_MARKERS:
        if marker in html[:5000]:
            return "FAILED_PAYWALL"
    return None


def fetch_page(session, url, domain, debug=False):
    """Fetch a URL. Returns (status_code, html, error_status, error_msg)."""
    headers = {}
    if domain == "nytimes.com":
        headers["Referer"] = "https://www.google.com/"

    for attempt in range(MAX_RETRIES):
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT, headers=headers)
            html = resp.text
            err = classify_response(resp, html)
            if err:
                if attempt < MAX_RETRIES - 1 and resp.status_code in (429, 502, 503):
                    wait = RETRY_BACKOFF * (attempt + 1) + random.uniform(0, 2)
                    if debug:
                        print(f"    retry {attempt+1} after {resp.status_code}, waiting {wait:.1f}s")
                    time.sleep(wait)
                    continue
                return resp.status_code, html, err, f"HTTP {resp.status_code}"
            return resp.status_code, html, None, None
        except Exception as e:
            err_str = str(e).lower()
            if "timeout" in err_str or "timed out" in err_str:
                status = "FAILED_TIMEOUT"
            else:
                status = "FAILED_NETWORK"
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF * (attempt + 1))
                continue
            return 0, "", status, str(e)[:200]

    return 0, "", "FAILED_NETWORK", "Max retries exceeded"


# ═════════════════════════════════════════════════════════════════════════
# PARSER UTILITIES
# ═════════════════════════════════════════════════════════════════════════


def extract_trafilatura(html):
    """Extract article text using trafilatura. Returns (headline, body) or ('', '')."""
    try:
        text = trafilatura.extract(html, include_comments=False, include_tables=False)
        if text and len(text) > 200:
            lines = text.strip().split("\n")
            headline = lines[0] if lines else ""
            return headline, text
    except Exception:
        pass
    return "", ""


def extract_ld_json(soup):
    """Extract headline and articleBody from LD+JSON if present."""
    headline = ""
    body = ""
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
            if isinstance(data, list):
                data = data[0]
            if not isinstance(data, dict):
                continue
            if not headline:
                headline = data.get("headline", "")
            if not body:
                body = data.get("articleBody", "")
        except Exception:
            pass
    return headline, body


def extract_generic_article(soup):
    """Generic fallback: find <article> and extract <p> tags."""
    headline = ""
    h1 = soup.find("h1")
    if h1:
        headline = h1.get_text(strip=True)

    article = soup.find("article")
    container = article if article else soup.find("main") or soup
    paragraphs = []
    for p in container.find_all("p"):
        text = p.get_text(strip=True)
        if len(text) > 30:
            paragraphs.append(text)
    return headline, "\n\n".join(paragraphs)


def clean_paragraphs(elements, min_len=20):
    """Extract text from a list of tag elements, filtering short ones."""
    parts = []
    for el in elements:
        text = el.get_text(strip=True)
        if len(text) >= min_len:
            parts.append(text)
    return "\n\n".join(parts)


# ═════════════════════════════════════════════════════════════════════════
# DOMAIN PARSERS
# ═════════════════════════════════════════════════════════════════════════

# Each returns (headline: str, body: str, tier: str)
# tier is for debug output: "site-specific", "generic-article", "ld-json"


# ── foxnews.com ──────────────────────────────────────────────────────────
# Selectors: h1.headline, div.article-body > p, LD+JSON fallback

def parse_foxnews(html, url):
    soup = BeautifulSoup(html, "html.parser")
    headline = ""
    body = ""

    h1 = soup.find("h1", class_="headline")
    if not h1:
        h1 = soup.find("h1")
    if h1:
        headline = h1.get_text(strip=True)

    body_div = soup.find("div", class_="article-body")
    if body_div:
        body = clean_paragraphs(body_div.find_all("p"))
        if body:
            return headline, body, "site-specific"

    h_ld, b_ld = extract_ld_json(soup)
    if b_ld:
        return headline or h_ld, b_ld, "ld-json"

    h_gen, b_gen = extract_generic_article(soup)
    if b_gen:
        return headline or h_gen, b_gen, "generic-article"

    return headline or h_ld or h_gen, "", "failed"


# ── thehill.com ──────────────────────────────────────────────────────────
# Selectors: h1.page-title, div.article__text > p, LD+JSON fallback

def parse_thehill(html, url):
    soup = BeautifulSoup(html, "html.parser")
    headline = ""
    body = ""

    h1 = soup.find("h1", class_="page-title")
    if not h1:
        h1 = soup.find("h1")
    if h1:
        headline = h1.get_text(strip=True)

    body_div = soup.find(class_="article__text")
    if body_div:
        body = clean_paragraphs(body_div.find_all("p"))
        if body:
            return headline, body, "site-specific"

    h_ld, b_ld = extract_ld_json(soup)
    if b_ld:
        return headline or h_ld, b_ld, "ld-json"

    h_gen, b_gen = extract_generic_article(soup)
    if b_gen:
        return headline or h_gen, b_gen, "generic-article"

    return headline or h_ld or h_gen, "", "failed"


# ── nytimes.com ──────────────────────────────────────────────────────────
# Selectors: h1, article > p (min 30 chars), LD+JSON articleBody fallback
# Network: Google referer header applied in fetch_page

def parse_nytimes(html, url):
    soup = BeautifulSoup(html, "html.parser")
    headline = ""

    h1 = soup.find("h1")
    if h1:
        headline = h1.get_text(strip=True)

    # Try LD+JSON first for NYT — more reliable than HTML parsing behind paywall
    h_ld, b_ld = extract_ld_json(soup)
    if b_ld and len(b_ld) > 200:
        return headline or h_ld, b_ld, "ld-json"

    article = soup.find("article")
    if article:
        body = clean_paragraphs(article.find_all("p"), min_len=30)
        if body and len(body) > 200:
            return headline, body, "site-specific"

    # trafilatura fallback — effective when curl_cffi gets past NYT's paywall
    h_traf, b_traf = extract_trafilatura(html)
    if b_traf:
        return headline or h_traf, b_traf, "trafilatura"

    h_gen, b_gen = extract_generic_article(soup)
    if b_gen and len(b_gen) > 200:
        return headline or h_gen, b_gen, "generic-article"

    return headline or h_ld or h_gen, b_ld or b_gen or "", "failed"


# ── apnews.com ───────────────────────────────────────────────────────────
# Selectors: h1, div.RichTextStoryBody > p, LD+JSON fallback

def parse_apnews(html, url):
    soup = BeautifulSoup(html, "html.parser")
    headline = ""

    h1 = soup.find("h1")
    if h1:
        headline = h1.get_text(strip=True)

    body_div = soup.find(class_=lambda c: c and "RichTextStoryBody" in str(c))
    if body_div:
        body = clean_paragraphs(body_div.find_all("p"))
        if body:
            return headline, body, "site-specific"

    h_ld, b_ld = extract_ld_json(soup)
    if b_ld:
        return headline or h_ld, b_ld, "ld-json"

    h_gen, b_gen = extract_generic_article(soup)
    if b_gen:
        return headline or h_gen, b_gen, "generic-article"

    return headline or h_ld or h_gen, "", "failed"


# ── cnn.com ──────────────────────────────────────────────────────────────
# Selectors: h1, div.article__content > p, LD+JSON articleBody fallback

def parse_cnn(html, url):
    soup = BeautifulSoup(html, "html.parser")
    headline = ""

    h1 = soup.find("h1")
    if h1:
        headline = h1.get_text(strip=True)

    body_div = soup.find(class_=lambda c: c and "article__content" in str(c))
    if body_div:
        body = clean_paragraphs(body_div.find_all("p"))
        if body:
            return headline, body, "site-specific"

    h_ld, b_ld = extract_ld_json(soup)
    if b_ld:
        return headline or h_ld, b_ld, "ld-json"

    h_gen, b_gen = extract_generic_article(soup)
    if b_gen:
        return headline or h_gen, b_gen, "generic-article"

    return headline or h_ld or h_gen, "", "failed"


# ── newsweek.com ─────────────────────────────────────────────────────────
# Selectors: h1, article > p, LD+JSON fallback

def parse_newsweek(html, url):
    soup = BeautifulSoup(html, "html.parser")
    headline = ""

    h1 = soup.find("h1")
    if h1:
        headline = h1.get_text(strip=True)

    article = soup.find("article")
    if article:
        body = clean_paragraphs(article.find_all("p"))
        if body:
            return headline, body, "site-specific"

    h_ld, b_ld = extract_ld_json(soup)
    if b_ld:
        return headline or h_ld, b_ld, "ld-json"

    h_gen, b_gen = extract_generic_article(soup)
    if b_gen:
        return headline or h_gen, b_gen, "generic-article"

    return headline or h_ld or h_gen, "", "failed"


# ── reuters.com ──────────────────────────────────────────────────────────
# Selectors: h1, div[data-testid="paragraph-N"], LD+JSON fallback
# Reuters renders article text in divs with data-testid="paragraph-0", etc.

def parse_reuters(html, url):
    soup = BeautifulSoup(html, "html.parser")
    headline = ""

    h1 = soup.find("h1")
    if h1:
        headline = h1.get_text(strip=True)

    paragraphs = []
    for div in soup.find_all("div", attrs={"data-testid": True}):
        tid = div.get("data-testid", "")
        if re.match(r"paragraph-\d+", tid):
            text = div.get_text(strip=True)
            if len(text) > 20:
                paragraphs.append(text)
    if paragraphs:
        return headline, "\n\n".join(paragraphs), "site-specific"

    body_div = soup.find(class_=lambda c: c and "article-body" in str(c))
    if body_div:
        body = clean_paragraphs(body_div.find_all("p"))
        if body:
            return headline, body, "site-specific"

    # trafilatura fallback — effective when curl_cffi gets past Reuters' JS challenge
    h_traf, b_traf = extract_trafilatura(html)
    if b_traf:
        return headline or h_traf, b_traf, "trafilatura"

    h_ld, b_ld = extract_ld_json(soup)
    if b_ld:
        return headline or h_ld, b_ld, "ld-json"

    h_gen, b_gen = extract_generic_article(soup)
    if b_gen:
        return headline or h_gen, b_gen, "generic-article"

    return headline or h_ld or h_gen, "", "failed"


# ── nypost.com ───────────────────────────────────────────────────────────
# Selectors: h1, div.single__content > p, LD+JSON fallback

def parse_nypost(html, url):
    soup = BeautifulSoup(html, "html.parser")
    headline = ""

    h1 = soup.find("h1")
    if h1:
        headline = h1.get_text(strip=True)

    body_div = soup.find(class_=lambda c: c and "single__content" in str(c))
    if body_div:
        body = clean_paragraphs(body_div.find_all("p"))
        if body:
            return headline, body, "site-specific"

    article = soup.find("article")
    if article:
        body = clean_paragraphs(article.find_all("p"))
        if body:
            return headline, body, "site-specific"

    h_ld, b_ld = extract_ld_json(soup)
    if b_ld:
        return headline or h_ld, b_ld, "ld-json"

    h_gen, b_gen = extract_generic_article(soup)
    if b_gen:
        return headline or h_gen, b_gen, "generic-article"

    return headline or h_ld or h_gen, "", "failed"


# ── washingtonexaminer.com ───────────────────────────────────────────────
# Selectors: h1, article.fn-body > p, LD+JSON fallback

def parse_washingtonexaminer(html, url):
    soup = BeautifulSoup(html, "html.parser")
    headline = ""

    h1 = soup.find("h1")
    if h1:
        headline = h1.get_text(strip=True)

    article = soup.find("article", class_="fn-body")
    if article:
        body = clean_paragraphs(article.find_all("p"))
        if body:
            return headline, body, "site-specific"

    article = soup.find("article")
    if article:
        body = clean_paragraphs(article.find_all("p"))
        if body:
            return headline, body, "site-specific"

    h_ld, b_ld = extract_ld_json(soup)
    if b_ld:
        return headline or h_ld, b_ld, "ld-json"

    h_gen, b_gen = extract_generic_article(soup)
    if b_gen:
        return headline or h_gen, b_gen, "generic-article"

    return headline or h_ld or h_gen, "", "failed"


# ── Parser registry ──────────────────────────────────────────────────────

PARSER_REGISTRY = {
    "foxnews.com": parse_foxnews,
    "thehill.com": parse_thehill,
    "nytimes.com": parse_nytimes,
    "apnews.com": parse_apnews,
    "cnn.com": parse_cnn,
    "newsweek.com": parse_newsweek,
    "reuters.com": parse_reuters,
    "nypost.com": parse_nypost,
    "washingtonexaminer.com": parse_washingtonexaminer,
}


# ═════════════════════════════════════════════════════════════════════════
# SCRAPE MODE
# ═════════════════════════════════════════════════════════════════════════


def scrape_item(session, item, debug=False):
    """Scrape one article. Returns the result dict for the output schema."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    domain = item["domain"]

    status_code, html, err_status, err_msg = fetch_page(session, item["url"], domain, debug)

    if err_status:
        return {
            "domain": domain,
            "url": item["url"],
            "rating": item["rating"],
            "scrape_timestamp": ts,
            "http_status_code": status_code,
            "execution_status": err_status,
            "extracted_headline": "",
            "extracted_body_text": "",
            "error_payload": err_msg,
        }

    parser = PARSER_REGISTRY[domain]
    headline, body, tier = parser(html, item["url"])

    if not body:
        return {
            "domain": domain,
            "url": item["url"],
            "rating": item["rating"],
            "scrape_timestamp": ts,
            "http_status_code": status_code,
            "execution_status": "FAILED_PARSE",
            "extracted_headline": headline,
            "extracted_body_text": "",
            "error_payload": f"Parser returned empty body (tier: {tier})",
        }

    if debug:
        print(f"    tier={tier}, headline={len(headline)}c, body={len(body)}c")

    return {
        "domain": domain,
        "url": item["url"],
        "rating": item["rating"],
        "scrape_timestamp": ts,
        "http_status_code": status_code,
        "execution_status": "SUCCESS",
        "extracted_headline": headline,
        "extracted_body_text": body,
        "error_payload": None,
    }


def run_scrape(args):
    buckets = load_input(args.input)
    master = load_master(args.output)

    # Apply filters
    work_items = []
    for domain, items in buckets.items():
        if args.domain and domain != args.domain:
            continue
        for item in items:
            if args.stance and item["slot"] != args.stance:
                continue
            work_items.append(item)

    # Filter out already-done
    pending = [w for w in work_items if not is_done(master, w["story_id"], w["slot"])]
    skipped = len(work_items) - len(pending)

    limited = False
    if args.limit and len(pending) > args.limit:
        pending = pending[:args.limit]
        limited = True

    msg = f"\nWork items: {len(work_items)} total, {skipped} already done, {len(pending)} to scrape"
    if limited:
        msg += f" (limited from {len(work_items) - skipped})"
    print(msg)

    if not pending:
        print("Nothing to scrape.")
        return

    session = make_session()
    success = 0
    failed = 0
    session_count = 0

    for i, item in enumerate(pending):
        if session_count >= 20:
            session = make_session()
            session_count = 0

        label = f"[{i+1}/{len(pending)}] {item['domain']} ({item['slot']}) {item['url'][:60]}"
        print(label, end=" ", flush=True)

        result = scrape_item(session, item, debug=args.debug)
        write_result(master, item["story_id"], item["slot"], result)
        session_count += 1

        if result["execution_status"] == "SUCCESS":
            success += 1
            body_len = len(result["extracted_body_text"])
            print(f"OK ({body_len}c)")
        else:
            failed += 1
            print(f"{result['execution_status']}")

        if (i + 1) % FLUSH_INTERVAL == 0:
            save_master(master, args.output)
            if args.debug:
                print(f"    [flushed to disk]")

        time.sleep(random.uniform(*DELAY_RANGE))

    save_master(master, args.output)
    print(f"\nDone. success={success}, failed={failed}")
    print(f"Output: {args.output}")


# ═════════════════════════════════════════════════════════════════════════
# PATCH MODE
# ═════════════════════════════════════════════════════════════════════════


def run_patch(args):
    master = load_master(args.output)
    if not master:
        print("No master file found — run --mode scrape first.")
        return

    buckets = load_input(args.input)
    url_to_item = {}
    for domain, items in buckets.items():
        for item in items:
            url_to_item[(item["story_id"], item["slot"])] = item

    to_patch = []
    for story_id, story in master.items():
        for slot in ("left", "center", "right"):
            entry = story.get(slot)
            if not entry:
                continue
            if args.domain and entry.get("domain") != args.domain:
                continue
            if args.stance and slot != args.stance:
                continue
            needs_fix = (
                entry.get("execution_status") != "SUCCESS"
                or len(entry.get("extracted_body_text", "")) == 0
            )
            if needs_fix:
                key = (story_id, slot)
                if key in url_to_item:
                    to_patch.append(url_to_item[key])

    # Shuffle to avoid hammering one domain sequentially
    random.shuffle(to_patch)

    print(f"Found {len(to_patch)} records to patch")

    if not to_patch:
        return

    fixed = 0
    still_broken = 0

    for i, item in enumerate(to_patch):
        # Fresh session per request for paywall domains (maximizes chance of getting through)
        session = make_session()

        print(f"[{i+1}/{len(to_patch)}] {item['domain']} {item['url'][:60]}", end=" ", flush=True)

        result = scrape_item(session, item, debug=args.debug)
        write_result(master, item["story_id"], item["slot"], result)

        if result["execution_status"] == "SUCCESS":
            fixed += 1
            print(f"FIXED ({len(result['extracted_body_text'])}c)")
        else:
            still_broken += 1
            print(f"{result['execution_status']}")

        if (i + 1) % FLUSH_INTERVAL == 0:
            save_master(master, args.output)

        if i < len(to_patch) - 1:
            time.sleep(random.uniform(*DELAY_RANGE))

    save_master(master, args.output)
    print(f"\nPatch done. fixed={fixed}, still_broken={still_broken}")


# ═════════════════════════════════════════════════════════════════════════
# AUDIT MODE
# ═════════════════════════════════════════════════════════════════════════


def run_audit(args):
    master = load_master(args.output)
    if not master:
        print("No master file found — run --mode scrape first.")
        return

    buckets = load_input(args.input)
    total_expected = sum(len(v) for v in buckets.values())

    # Collect all entries
    all_entries = []
    for story_id, story in master.items():
        for slot in ("left", "center", "right"):
            entry = story.get(slot)
            if entry:
                all_entries.append({
                    "story_id": story_id,
                    "slot": slot,
                    **entry,
                })

    # ── Section 1: Completeness Matrix ──
    status_counts = {}
    for e in all_entries:
        s = e.get("execution_status", "UNKNOWN")
        status_counts[s] = status_counts.get(s, 0) + 1

    total_scraped = len(all_entries)
    success_count = status_counts.get("SUCCESS", 0)

    print("# Audit Report\n")
    print("## 1. Completeness Matrix\n")
    print(f"| Metric | Count | % |")
    print(f"|---|---|---|")
    print(f"| Expected in-scope slots | {total_expected} | 100% |")
    print(f"| Scraped (any status) | {total_scraped} | {100*total_scraped/max(total_expected,1):.1f}% |")
    print(f"| SUCCESS | {success_count} | {100*success_count/max(total_expected,1):.1f}% |")
    for status, count in sorted(status_counts.items()):
        if status != "SUCCESS":
            print(f"| {status} | {count} | {100*count/max(total_expected,1):.1f}% |")
    not_attempted = total_expected - total_scraped
    if not_attempted > 0:
        print(f"| NOT_ATTEMPTED | {not_attempted} | {100*not_attempted/max(total_expected,1):.1f}% |")

    # ── Section 2: Failure Analysis by Domain ──
    domain_stats = {}
    for e in all_entries:
        d = e.get("domain", "unknown")
        if d not in domain_stats:
            domain_stats[d] = {"total": 0, "SUCCESS": 0}
        domain_stats[d]["total"] += 1
        s = e.get("execution_status", "UNKNOWN")
        domain_stats[d][s] = domain_stats[d].get(s, 0) + 1

    all_statuses = sorted(set(s for d in domain_stats.values() for s in d if s not in ("total",)))

    print("\n## 2. Failure Analysis by Domain\n")
    header = "| Domain | Total | " + " | ".join(all_statuses) + " |"
    sep = "|---|---|" + "|".join(["---"] * len(all_statuses)) + "|"
    print(header)
    print(sep)
    for d in sorted(domain_stats, key=lambda x: domain_stats[x]["total"], reverse=True):
        ds = domain_stats[d]
        row = f"| {d} | {ds['total']} | "
        row += " | ".join(str(ds.get(s, 0)) for s in all_statuses)
        row += " |"
        print(row)

    # ── Section 3: Low-Quality Flags ──
    low_quality = []
    for e in all_entries:
        if e.get("execution_status") == "SUCCESS":
            body = e.get("extracted_body_text", "")
            if len(body) < 200:
                low_quality.append(e)

    print("\n## 3. Low-Quality Flags (SUCCESS but < 200 chars)\n")
    if not low_quality:
        print("None found.\n")
    else:
        print(f"Found {len(low_quality)} records:\n")
        for e in low_quality[:50]:
            body_preview = e.get("extracted_body_text", "")[:80].replace("\n", " ")
            print(f"- `{e['story_id']}.{e['slot']}` ({e['domain']}): {len(e.get('extracted_body_text',''))} chars -- \"{body_preview}\"")
        if len(low_quality) > 50:
            print(f"- ... and {len(low_quality) - 50} more")


# ═════════════════════════════════════════════════════════════════════════
# CLI
# ═════════════════════════════════════════════════════════════════════════


def main():
    parser = argparse.ArgumentParser(
        description="AllSides Featured-Article Scraper"
    )
    parser.add_argument("--mode", choices=["scrape", "patch", "audit"], default="scrape")
    parser.add_argument("--domain", help="Filter to a single domain (e.g., foxnews.com)")
    parser.add_argument("--stance", choices=["left", "center", "right"])
    parser.add_argument("--limit", type=int, help="Max articles to scrape (for testing)")
    parser.add_argument("--debug", action="store_true", help="Print per-URL parser tier and char counts")
    parser.add_argument("--input", default=DEFAULT_INPUT, help="Path to input JSONL")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Path to master output JSON")

    args = parser.parse_args()

    if args.domain and args.domain not in TARGET_DOMAINS:
        print(f"Error: '{args.domain}' is not one of the 9 target domains:")
        for d in sorted(TARGET_DOMAINS):
            print(f"  {d}")
        sys.exit(1)

    if args.mode == "scrape":
        run_scrape(args)
    elif args.mode == "patch":
        run_patch(args)
    elif args.mode == "audit":
        run_audit(args)


if __name__ == "__main__":
    main()
