"""
Re-scrape NYT articles that came back empty from the initial allsides run.
Uses fresh sessions, longer delays, and rotated browser profiles.
"""
import json
import os
import time
import random
from datetime import datetime
from curl_cffi import requests
from nytimes_scraper import parse_article_page

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
NYT_OUTPUT = os.path.join(SCRIPT_DIR, "output", "nytimes_allsides.jsonl")
NYT_OUTPUT_NEW = os.path.join(SCRIPT_DIR, "output", "nytimes_allsides_new.jsonl")

PROFILES = ["chrome", "chrome110", "chrome116", "chrome120", "chrome124"]
DELAY_MIN = 4.0
DELAY_MAX = 7.0
SESSION_ROTATE_EVERY = 10


def make_session():
    profile = random.choice(PROFILES)
    return requests.Session(impersonate=profile)


def main():
    with open(NYT_OUTPUT) as f:
        records = [json.loads(line) for line in f if line.strip()]

    empty = [r for r in records if not r.get("scraped_body")]
    has_body = [r for r in records if r.get("scraped_body")]
    print(f"Total records: {len(records)}")
    print(f"Already have body: {len(has_body)}")
    print(f"Need re-scrape: {len(empty)}")

    session = make_session()
    fixed = 0
    still_empty = 0

    for i, rec in enumerate(empty):
        if i > 0 and i % SESSION_ROTATE_EVERY == 0:
            session = make_session()
            print(f"  [Rotated session at {i}]")

        url = rec["url"]
        print(f"  [{i+1}/{len(empty)}] {url[:80]}...", end=" ", flush=True)

        try:
            details = parse_article_page(session, url)
            if details.get("body"):
                rec["scraped_title"] = details["title"]
                rec["scraped_subtitle"] = details.get("subtitle", "")
                rec["scraped_author"] = details["author"]
                rec["scraped_date"] = details.get("date", "")
                rec["scraped_date_iso"] = details.get("date_iso", "")
                rec["scraped_body"] = details["body"]
                rec["scraped_image"] = details.get("image", "")
                rec["scraped_at"] = datetime.utcnow().isoformat() + "Z"
                rec.pop("error", None)
                fixed += 1
                print(f"OK ({len(details['body'])} chars)")
            else:
                still_empty += 1
                print("(still empty)")
        except Exception as e:
            still_empty += 1
            print(f"FAILED: {e}")

        time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

    all_records = has_body + empty
    with open(NYT_OUTPUT_NEW, "w") as f:
        for rec in all_records:
            json.dump(rec, f)
            f.write("\n")

    print(f"\nDone. Fixed: {fixed}, Still empty: {still_empty}")
    print(f"Output: {NYT_OUTPUT_NEW}")

    if fixed > 0:
        os.replace(NYT_OUTPUT_NEW, NYT_OUTPUT)
        print(f"Replaced {NYT_OUTPUT} with updated data.")


if __name__ == "__main__":
    main()
