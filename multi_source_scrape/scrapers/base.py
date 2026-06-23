"""
Shared framework for per-domain AllSides article scrapers.

Each domain scraper imports this module and calls run_scraper() with its
domain name and parse function. This handles input loading, network fetching,
resume logic, and per-domain output persistence.

Usage from a domain scraper:
    from base import run_scraper
    def parse(html, url): ...
    if __name__ == "__main__":
        run_scraper("bbc.com", parse)
"""

import argparse
import hashlib
import json
import os
import random
import re
import tempfile
import time
from datetime import datetime, timezone
from urllib.parse import unquote, urlparse

from curl_cffi import requests
from bs4 import BeautifulSoup

try:
    import trafilatura
except ImportError:
    trafilatura = None

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
REPO_ROOT = os.path.dirname(PROJECT_DIR)
DEFAULT_INPUT = os.path.join(REPO_ROOT, "allsides_crawl", "output", "allsides_jan2025_may2026_combined.jsonl")
PER_DOMAIN_DIR = os.path.join(PROJECT_DIR, "output", "per_domain")
IMAGES_DIR = os.path.join(PROJECT_DIR, "output", "images")

CHROME_PROFILES = ["chrome", "chrome110", "chrome120"]
REQUEST_TIMEOUT = 20
DELAY_RANGE = (3.0, 7.0)
MAX_RETRIES = 2
RETRY_BACKOFF = 5
FLUSH_INTERVAL = 25
SESSION_ROTATE_EVERY = 15
LONG_PAUSE_EVERY = 30
LONG_PAUSE_RANGE = (15.0, 30.0)
CONSECUTIVE_FAIL_PAUSE = 20.0

CLOUDFLARE_MARKERS = [
    "cf-browser-verification",
    "cf_chl_opt",
    "Checking your browser",
    "challenges.cloudflare.com",
]


_MAX_FNAME_LEN = 120


def _sanitize_filename(name):
    name = re.sub(r'[^\w\-.]', '_', name)
    return name[:_MAX_FNAME_LEN]


def _filename_from_url(url, index):
    path = unquote(urlparse(url).path)
    basename = os.path.basename(path) or f"image_{index}"
    basename = _sanitize_filename(basename)
    if not re.search(r'\.(jpg|jpeg|png|gif|webp|svg|bmp|avif)$', basename, re.I):
        basename += ".jpg"
    return f"{index:03d}_{basename}"


def download_article_images(session, result, story_id, slot,
                            images_dir=None, output_base=None, debug=False):
    """Download extracted images to disk, adding local_path to each entry."""
    images = result.get("extracted_images")
    if not images:
        return
    images_dir = images_dir or IMAGES_DIR
    output_base = output_base or os.path.join(PROJECT_DIR, "output")
    domain = result["domain"]
    dest_dir = os.path.join(images_dir, domain, story_id, slot)

    for i, img in enumerate(images):
        url = img.get("url", "")
        if not url or not url.startswith("http"):
            continue
        fname = _filename_from_url(url, i)
        dest = os.path.join(dest_dir, fname)
        rel_path = os.path.relpath(dest, output_base)
        try:
            os.makedirs(dest_dir, exist_ok=True)
            resp = session.get(url, timeout=15)
            ct = resp.headers.get("Content-Type", "")
            if resp.status_code == 200 and (ct.startswith("image/") or "octet-stream" in ct):
                if len(resp.content) >= 100:
                    with open(dest, "wb") as f:
                        f.write(resp.content)
                    img["local_path"] = rel_path
                    if debug:
                        print(f"    [img {i}: {len(resp.content)} bytes]")
                    continue
        except Exception as e:
            if debug:
                print(f"    [img {i}: failed: {e}]")


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


def load_input_for_domain(input_path, target_domain):
    """Read JSONL, return list of work items for a single domain."""
    work_items = []
    with open(input_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            story_id = derive_story_id(record)
            for slot in ("left", "center", "right"):
                val = record.get(slot)
                if not isinstance(val, dict):
                    continue
                link = val.get("link", "")
                if not link:
                    continue
                domain = normalize_domain(link)
                if domain != target_domain:
                    continue
                work_items.append({
                    "story_id": story_id,
                    "slot": slot,
                    "url": link,
                    "source": val.get("source", ""),
                    "rating": val.get("rating", ""),
                    "domain": domain,
                })
    return work_items


def load_domain_output(domain, output_dir=None):
    d = output_dir or PER_DOMAIN_DIR
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{domain}.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def save_domain_output(domain, data, output_dir=None):
    d = output_dir or PER_DOMAIN_DIR
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{domain}.json")
    fd, tmp_path = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def is_done(output, story_id, slot):
    story = output.get(story_id)
    if not story:
        return False
    entry = story.get(slot)
    if not entry:
        return False
    return entry.get("execution_status") == "SUCCESS"


def needs_patch(output, story_id, slot):
    story = output.get(story_id)
    if not story:
        return False
    entry = story.get(slot)
    if not entry:
        return False
    return (
        entry.get("execution_status") != "SUCCESS"
        or len(entry.get("extracted_body_text", "")) == 0
    )


def needs_refresh(output, story_id, slot):
    story = output.get(story_id)
    if not story:
        return False
    entry = story.get(slot)
    if not entry:
        return False
    if entry.get("execution_status") != "SUCCESS":
        return False
    imgs = entry.get("extracted_images", [])
    if imgs and all(not img.get("caption") for img in imgs):
        return True
    if len(entry.get("extracted_body_text", "")) < 200:
        return True
    return False


def write_result(output, story_id, slot, result):
    if story_id not in output:
        output[story_id] = {}
    output[story_id][slot] = result


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


def fetch_page(session, url, domain, extra_headers=None, debug=False):
    headers = dict(extra_headers or {})

    for attempt in range(MAX_RETRIES):
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT, headers=headers)
            html = resp.text
            err = classify_response(resp, html)
            if err:
                if debug:
                    print(f"  [attempt {attempt+1}] {err} (HTTP {resp.status_code})")
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_BACKOFF * (attempt + 1))
                    continue
                return resp.status_code, html, err, f"HTTP {resp.status_code}"
            return resp.status_code, html, None, None
        except requests.errors.RequestsError as e:
            if "timeout" in str(e).lower():
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_BACKOFF * (attempt + 1))
                    continue
                return 0, "", "FAILED_TIMEOUT", str(e)
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF * (attempt + 1))
                continue
            return 0, "", "FAILED_NETWORK", str(e)
        except Exception as e:
            return 0, "", "FAILED_NETWORK", str(e)

    return 0, "", "FAILED_NETWORK", "exhausted retries"


def extract_trafilatura(html):
    if trafilatura is None:
        return "", ""
    result = trafilatura.extract(html, include_comments=False, include_tables=False)
    if not result or len(result) < 100:
        return "", ""
    lines = result.strip().split("\n")
    headline = lines[0] if lines else ""
    body = "\n".join(lines[1:]).strip() if len(lines) > 1 else result
    return headline, body


def scrape_item(session, item, parse_fn, extra_headers=None, debug=False, body_filter=None):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    status_code, html, err_status, err_msg = fetch_page(
        session, item["url"], item["domain"],
        extra_headers=extra_headers, debug=debug
    )

    if err_status:
        return {
            "domain": item["domain"],
            "url": item["url"],
            "rating": item["rating"],
            "scrape_timestamp": ts,
            "http_status_code": status_code,
            "execution_status": err_status,
            "extracted_headline": "",
            "extracted_body_text": "",
            "error_payload": err_msg,
        }

    try:
        result_tuple = parse_fn(html, item["url"])
        if len(result_tuple) == 5:
            headline, body, images, videos, interactives = result_tuple
        elif len(result_tuple) == 4:
            headline, body, images, interactives = result_tuple
            videos = []
        elif len(result_tuple) == 3:
            headline, body, images = result_tuple
            videos = []
            interactives = []
        else:
            headline, body = result_tuple
            images = []
            videos = []
            interactives = []
    except Exception as e:
        headline, body, images, videos, interactives = "", "", [], [], []
        if debug:
            print(f"  [parse error: {e}]")

    if not body or len(body) < 50:
        tf_headline, tf_body = extract_trafilatura(html)
        if tf_body and len(tf_body) > len(body or ""):
            headline = tf_headline or headline
            body = tf_body
            if debug:
                print(f"  [trafilatura fallback: {len(body)}c]")

    if body and body_filter and not body_filter(body):
        if debug:
            print(f"  [body_filter rejected: {len(body)}c]")
        body = ""

    if not body or len(body) < 50:
        result = {
            "domain": item["domain"],
            "url": item["url"],
            "rating": item["rating"],
            "scrape_timestamp": ts,
            "http_status_code": status_code,
            "execution_status": "FAILED_PARSE",
            "extracted_headline": headline or "",
            "extracted_body_text": body or "",
            "error_payload": "body too short or empty after all parse tiers",
        }
        if images:
            result["extracted_images"] = images
        if videos:
            result["extracted_videos"] = videos
        if interactives:
            result["extracted_interactives"] = interactives
        return result

    result = {
        "domain": item["domain"],
        "url": item["url"],
        "rating": item["rating"],
        "scrape_timestamp": ts,
        "http_status_code": status_code,
        "execution_status": "SUCCESS",
        "extracted_headline": headline,
        "extracted_body_text": body,
        "error_payload": None,
    }
    if images:
        result["extracted_images"] = images
    if videos:
        result["extracted_videos"] = videos
    if interactives:
        result["extracted_interactives"] = interactives
    return result


def run_scraper(domain, parse_fn, extra_headers=None, body_filter=None):
    parser = argparse.ArgumentParser(
        description=f"AllSides scraper for {domain}"
    )
    parser.add_argument("--mode", choices=["scrape", "patch", "refresh", "audit"], default="scrape")
    parser.add_argument("--limit", type=int, help="Max articles to scrape")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--stance", choices=["left", "center", "right"])
    parser.add_argument("--output-dir", help="Override output base directory")
    args = parser.parse_args()

    if args.output_dir:
        per_domain_dir = os.path.join(args.output_dir, "per_domain")
        images_dir = os.path.join(args.output_dir, "images")
        output_base = args.output_dir
    else:
        per_domain_dir = PER_DOMAIN_DIR
        images_dir = IMAGES_DIR
        output_base = os.path.join(PROJECT_DIR, "output")

    work_items = load_input_for_domain(args.input, domain)
    print(f"{domain}: {len(work_items)} total article slots in corpus")

    if args.stance:
        work_items = [w for w in work_items if w["slot"] == args.stance]
        print(f"  filtered to stance={args.stance}: {len(work_items)}")

    output = load_domain_output(domain, per_domain_dir)

    if args.mode == "audit":
        run_audit(domain, output, work_items)
        return

    if args.mode == "patch":
        pending = [w for w in work_items if needs_patch(output, w["story_id"], w["slot"])]
        random.shuffle(pending)
        label = "patch"
    elif args.mode == "refresh":
        pending = [w for w in work_items if needs_refresh(output, w["story_id"], w["slot"])]
        random.shuffle(pending)
        label = "refresh"
    else:
        pending = [w for w in work_items if not is_done(output, w["story_id"], w["slot"])]
        label = "scrape"

    skipped = len(work_items) - len(pending)

    limited = False
    if args.limit and len(pending) > args.limit:
        pending = pending[:args.limit]
        limited = True

    msg = f"  {skipped} already done, {len(pending)} to {label}"
    if limited:
        msg += f" (limited from {len(work_items) - skipped})"
    print(msg)

    if not pending:
        print("Nothing to do.")
        return

    session = make_session()
    success = 0
    failed = 0
    session_count = 0
    consecutive_fails = 0

    for i, item in enumerate(pending):
        if args.mode in ("patch", "refresh"):
            session = make_session()
        elif session_count >= SESSION_ROTATE_EVERY:
            session = make_session()
            session_count = 0
            pause = random.uniform(5.0, 10.0)
            print(f"  [new session, pausing {pause:.0f}s]")
            time.sleep(pause)

        short_url = item["url"][:65]
        print(f"[{i+1}/{len(pending)}] ({item['slot']}) {short_url}", end=" ", flush=True)

        result = scrape_item(session, item, parse_fn,
                             extra_headers=extra_headers, debug=args.debug,
                             body_filter=body_filter)
        download_article_images(session, result, item["story_id"], item["slot"],
                                images_dir=images_dir, output_base=output_base,
                                debug=args.debug)
        write_result(output, item["story_id"], item["slot"], result)
        session_count += 1

        if result["execution_status"] == "SUCCESS":
            success += 1
            consecutive_fails = 0
            print(f"OK ({len(result['extracted_body_text'])}c)")
        else:
            failed += 1
            consecutive_fails += 1
            print(result["execution_status"])
            if consecutive_fails >= 3:
                pause = CONSECUTIVE_FAIL_PAUSE * consecutive_fails
                print(f"  [{consecutive_fails} consecutive fails — backing off {pause:.0f}s]")
                time.sleep(pause)
                session = make_session()
                session_count = 0
            if consecutive_fails >= 10:
                print(f"  [10 consecutive fails — stopping to avoid ban]")
                break

        if (i + 1) % FLUSH_INTERVAL == 0:
            save_domain_output(domain, output, per_domain_dir)
            if args.debug:
                print("  [flushed]")

        if i < len(pending) - 1:
            if (i + 1) % LONG_PAUSE_EVERY == 0:
                pause = random.uniform(*LONG_PAUSE_RANGE)
                print(f"  [long pause {pause:.0f}s]")
                time.sleep(pause)
            else:
                time.sleep(random.uniform(*DELAY_RANGE))

    save_domain_output(domain, output, per_domain_dir)
    print(f"\nDone. success={success}, failed={failed}")
    out_path = os.path.join(per_domain_dir, f"{domain}.json")
    print(f"Output: {out_path}")


def run_audit(domain, output, work_items):
    total = len(work_items)
    done_ids = set()
    status_counts = {}
    low_quality = []

    for story_id, story in output.items():
        for slot, entry in story.items():
            status = entry.get("execution_status", "UNKNOWN")
            status_counts[status] = status_counts.get(status, 0) + 1
            if status == "SUCCESS":
                done_ids.add((story_id, slot))
                body = entry.get("extracted_body_text", "")
                if len(body) < 200:
                    low_quality.append((story_id, slot, len(body), entry.get("url", "")))

    scraped = sum(1 for w in work_items if (w["story_id"], w["slot"]) in done_ids)
    entries_total = sum(status_counts.values())

    print(f"\n## {domain} Audit")
    print(f"\nCorpus slots: {total}")
    print(f"Entries in output: {entries_total}")
    print(f"SUCCESS: {status_counts.get('SUCCESS', 0)}/{total} ({100*status_counts.get('SUCCESS',0)/max(total,1):.1f}%)")

    print(f"\n### Status breakdown")
    for status, count in sorted(status_counts.items(), key=lambda x: -x[1]):
        print(f"  {status}: {count}")

    if low_quality:
        print(f"\n### Low-quality (SUCCESS but <200 chars): {len(low_quality)}")
        for sid, slot, chars, url in low_quality[:20]:
            print(f"  {sid} [{slot}] {chars}c {url[:60]}")

    remaining = total - scraped
    print(f"\n### Remaining: {remaining} slots not yet SUCCESS")
