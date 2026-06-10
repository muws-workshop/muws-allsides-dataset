"""
Supporting scraper: fix missing/broken data in existing JSONL outputs.

Scans all 6 output files, identifies records with missing fields,
re-scrapes only those URLs, and updates the files in-place (with backup).

Issues fixed:
  - nytimes_allsides/top: missing body, title, author, image (paywall failures)
  - thehill_allsides/top: subtitle contains login prompt instead of real subtitle
  - foxnews_allsides: handful of missing body/title records
  - Any other source with missing scraped_body/body

Usage:
    conda activate scrap2
    python fix_missing.py              # fix all files
    python fix_missing.py --dry-run    # show what would be fixed without scraping
    python fix_missing.py --source nytimes  # fix only nytimes files
"""

import json
import os
import sys
import time
import random
import shutil
from datetime import datetime

from foxnews_scraper import parse_article_page as fox_parse, make_session as fox_session
from nytimes_scraper import parse_article_page as nyt_parse, make_session as nyt_session
from thehill_scraper import parse_article_page as hill_parse, make_session as hill_session

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output")
BACKUP_DIR = os.path.join(OUTPUT_DIR, "backups")

THEHILL_LOGIN_PROMPT = "one-click link to sign in"

NYT_PROFILES = ["chrome", "chrome110", "chrome116", "chrome120", "chrome124"]
NYT_SESSION_ROTATE_EVERY = 10
NYT_DELAY = (4.0, 7.0)
DEFAULT_DELAY = (1.5, 3.5)

FILES = {
    "foxnews_allsides": {
        "file": "foxnews_allsides.jsonl",
        "schema": "allsides",
        "source": "foxnews",
        "make_session": fox_session,
        "parse": fox_parse,
        "delay": DEFAULT_DELAY,
    },
    "foxnews_top": {
        "file": "foxnews_top.jsonl",
        "schema": "top",
        "source": "foxnews",
        "make_session": fox_session,
        "parse": fox_parse,
        "delay": DEFAULT_DELAY,
    },
    "nytimes_allsides": {
        "file": "nytimes_allsides.jsonl",
        "schema": "allsides",
        "source": "nytimes",
        "make_session": lambda: __import__("curl_cffi").requests.Session(impersonate=random.choice(NYT_PROFILES)),
        "parse": nyt_parse,
        "delay": NYT_DELAY,
        "rotate_every": NYT_SESSION_ROTATE_EVERY,
    },
    "nytimes_top": {
        "file": "nytimes_top.jsonl",
        "schema": "top",
        "source": "nytimes",
        "make_session": lambda: __import__("curl_cffi").requests.Session(impersonate=random.choice(NYT_PROFILES)),
        "parse": nyt_parse,
        "delay": NYT_DELAY,
        "rotate_every": NYT_SESSION_ROTATE_EVERY,
    },
    "thehill_allsides": {
        "file": "thehill_allsides.jsonl",
        "schema": "allsides",
        "source": "thehill",
        "make_session": hill_session,
        "parse": hill_parse,
        "delay": DEFAULT_DELAY,
    },
    "thehill_top": {
        "file": "thehill_top.jsonl",
        "schema": "top",
        "source": "thehill",
        "make_session": hill_session,
        "parse": hill_parse,
        "delay": DEFAULT_DELAY,
    },
}


def needs_fix(record, schema, source):
    """Check if a record has missing/broken data that warrants re-scraping."""
    reasons = []

    if schema == "allsides":
        body_key, title_key, sub_key = "scraped_body", "scraped_title", "scraped_subtitle"
    else:
        body_key, title_key, sub_key = "body", "headline", "subtitle"

    if not record.get(body_key):
        reasons.append("missing_body")
    if not record.get(title_key):
        reasons.append("missing_title")

    if source == "thehill":
        sub = record.get(sub_key, "")
        if sub and THEHILL_LOGIN_PROMPT in sub:
            reasons.append("login_prompt_subtitle")

    return reasons


def apply_fix(record, details, schema, source, reasons):
    """Apply scraped details to the record, only updating empty/broken fields."""
    if schema == "allsides":
        field_map = {
            "title": "scraped_title",
            "subtitle": "scraped_subtitle",
            "author": "scraped_author",
            "date": "scraped_date",
            "date_iso": "scraped_date_iso",
            "body": "scraped_body",
            "image": "scraped_image",
            "category": "scraped_category",
            "tags": "scraped_tags",
        }
    else:
        field_map = {
            "title": "headline",
            "subtitle": "description" if source == "nytimes" else "subtitle",
            "author": "author",
            "date_iso": "date_iso",
            "body": "body",
            "image": "image",
        }

    for detail_key, record_key in field_map.items():
        new_val = details.get(detail_key, "")
        if not new_val:
            continue
        old_val = record.get(record_key, "")
        should_update = False

        if not old_val:
            should_update = True
        elif record_key in ("scraped_subtitle", "subtitle") and THEHILL_LOGIN_PROMPT in str(old_val):
            should_update = True

        if should_update:
            record[record_key] = new_val

    record["scraped_at"] = datetime.utcnow().isoformat() + "Z"
    record.pop("error", None)
    return record


def process_file(key, config, dry_run=False):
    """Process a single JSONL file: find broken records, re-scrape, update."""
    filepath = os.path.join(OUTPUT_DIR, config["file"])
    if not os.path.exists(filepath):
        print(f"  SKIP {config['file']}: file not found")
        return

    with open(filepath) as f:
        records = [json.loads(line) for line in f if line.strip()]

    to_fix = []
    for i, rec in enumerate(records):
        reasons = needs_fix(rec, config["schema"], config["source"])
        if reasons:
            to_fix.append((i, rec, reasons))

    total = len(records)
    fix_count = len(to_fix)
    ok_count = total - fix_count

    print(f"\n{'='*60}")
    print(f"  {config['file']}")
    print(f"  Total: {total}  |  OK: {ok_count}  |  Need fix: {fix_count}")

    if fix_count == 0:
        print(f"  Nothing to fix.")
        return

    reason_counts = {}
    for _, _, reasons in to_fix:
        for r in reasons:
            reason_counts[r] = reason_counts.get(r, 0) + 1
    for reason, count in sorted(reason_counts.items()):
        print(f"    {reason}: {count}")

    # Split into subtitle-only fixes (no scrape needed) vs real scrape jobs
    subtitle_only = [(i, rec, r) for i, rec, r in to_fix if r == ["login_prompt_subtitle"]]
    need_scrape = [(i, rec, r) for i, rec, r in to_fix if r != ["login_prompt_subtitle"]]

    if dry_run:
        if subtitle_only:
            print(f"  [DRY RUN] Would clear {len(subtitle_only)} bad subtitles (no scrape)")
        if need_scrape:
            print(f"  [DRY RUN] Would re-scrape {len(need_scrape)} URLs")
        return

    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(BACKUP_DIR, f"{config['file']}.{ts}.bak")
    shutil.copy2(filepath, backup_path)
    print(f"  Backup: {backup_path}")

    # Fix subtitle-only records instantly (no network needed)
    if subtitle_only:
        sub_key = "scraped_subtitle" if config["schema"] == "allsides" else "subtitle"
        for idx, rec, _ in subtitle_only:
            rec[sub_key] = ""
            records[idx] = rec
        print(f"  Cleared {len(subtitle_only)} bad subtitles (no scrape needed)")

    if not need_scrape:
        with open(filepath, "w") as f:
            for rec in records:
                json.dump(rec, f)
                f.write("\n")
        print(f"  Updated: {filepath}")
        return

    session = config["make_session"]()
    parse_fn = config["parse"]
    delay_min, delay_max = config["delay"]
    rotate_every = config.get("rotate_every", 0)

    scrape_count = len(need_scrape)
    fixed = 0
    still_broken = 0

    for j, (idx, rec, reasons) in enumerate(need_scrape):
        if rotate_every and j > 0 and j % rotate_every == 0:
            session = config["make_session"]()
            print(f"  [Rotated session at {j}]")

        url = rec["url"]
        print(f"  [{j+1}/{scrape_count}] {url[:80]}...", end=" ", flush=True)

        try:
            details = parse_fn(session, url)
            body_val = details.get("body", "")

            records[idx] = apply_fix(rec, details, config["schema"], config["source"], reasons)

            if body_val or "missing_body" not in reasons:
                fixed += 1
                print("FIXED")
            else:
                still_broken += 1
                print("(still no body)")
        except Exception as e:
            still_broken += 1
            print(f"FAILED: {e}")

        time.sleep(random.uniform(delay_min, delay_max))

    with open(filepath, "w") as f:
        for rec in records:
            json.dump(rec, f)
            f.write("\n")

    print(f"  Done: scraped={fixed+still_broken}, fixed={fixed}, still_broken={still_broken}")
    print(f"  Updated: {filepath}")


def main():
    dry_run = "--dry-run" in sys.argv
    source_filter = None
    if "--source" in sys.argv:
        idx = sys.argv.index("--source")
        if idx + 1 < len(sys.argv):
            source_filter = sys.argv[idx + 1]

    print(f"Fix Missing Data — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if dry_run:
        print("[DRY RUN MODE]")

    for key, config in FILES.items():
        if source_filter and source_filter not in key:
            continue
        process_file(key, config, dry_run=dry_run)

    print(f"\n{'='*60}")
    print("All done.")
    if not dry_run:
        print(f"Backups saved in: {BACKUP_DIR}")


if __name__ == "__main__":
    main()
