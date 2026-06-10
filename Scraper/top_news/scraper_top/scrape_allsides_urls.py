"""
Scrape Fox News, NYTimes, and The Hill article URLs from the AllSides dataset.

Reads the allsides JSONL file, extracts URLs for matching sources from the
left/right/center and more_left/more_center/more_right fields, then uses
the existing scraper parse_article_page functions to fetch article content.

Usage:
    conda activate scrap2
    python scrape_allsides_urls.py
"""

import json
import os
import sys
import time
import random
from datetime import datetime

# Import parse functions from sibling scrapers
from foxnews_scraper import parse_article_page as fox_parse, make_session as fox_session
from nytimes_scraper import parse_article_page as nyt_parse, make_session as nyt_session
from thehill_scraper import parse_article_page as hill_parse, make_session as hill_session

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output")
ALLSIDES_FILE = os.path.join(
    SCRIPT_DIR, "..", "..", "output_2025_2026", "allsides_jan2025_may2026_combined.jsonl"
)

# Output files
FOX_OUTPUT = os.path.join(OUTPUT_DIR, "foxnews_allsides.jsonl")
NYT_OUTPUT = os.path.join(OUTPUT_DIR, "nytimes_allsides.jsonl")
HILL_OUTPUT = os.path.join(OUTPUT_DIR, "thehill_allsides.jsonl")

# Source name mapping
SOURCE_MAP = {
    "Fox News Digital": "foxnews",
    "New York Times (News)": "nytimes",
    "The Hill": "thehill",
}

# Delay between requests (seconds) - randomized within range
DELAY_MIN = 1.0
DELAY_MAX = 3.0


def extract_entries(allsides_file):
    """
    Read the allsides JSONL and extract all entries for our target sources.

    Returns a dict keyed by source tag ('foxnews', 'nytimes', 'thehill'),
    each containing a list of dicts with:
        - url: the article link
        - allsides_headline: the allsides story headline
        - allsides_topic: the topic
        - allsides_tags: the tags
        - allsides_date: the allsides date
        - bias_side: which side this source was listed under (left/right/center/more_left/etc.)
        - source_headline: the headline from the source entry
        - source_summary: the summary from the source entry
        - source_rating: the bias rating from allsides
        - source_image_link: the image link from allsides entry
        - news_type: the news type (NEWS/ANALYSIS/OPINION)
    """
    entries = {"foxnews": [], "nytimes": [], "thehill": []}
    seen_urls = {"foxnews": set(), "nytimes": set(), "thehill": set()}

    with open(allsides_file, "r") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                print(f"  Warning: invalid JSON on line {line_num}, skipping")
                continue

            allsides_meta = {
                "allsides_headline": record.get("headline", ""),
                "allsides_topic": record.get("topic", ""),
                "allsides_tags": record.get("tags", []),
                "allsides_date": record.get("date", ""),
                "allsides_headline_link": record.get("headline_link", ""),
            }

            # Check left/right/center (single dict each)
            for side in ["left", "right", "center"]:
                val = record.get(side)
                if not isinstance(val, dict):
                    continue
                source = val.get("source", "")
                link = val.get("link", "")
                if source in SOURCE_MAP and link:
                    tag = SOURCE_MAP[source]
                    if link not in seen_urls[tag]:
                        seen_urls[tag].add(link)
                        entry = {
                            "url": link,
                            "bias_side": side,
                            "source_headline": val.get("headline", ""),
                            "source_summary": val.get("summary", ""),
                            "source_rating": val.get("rating", ""),
                            "source_image_link": val.get("image_link", ""),
                            "news_type": val.get("news_type", ""),
                        }
                        entry.update(allsides_meta)
                        entries[tag].append(entry)

            # Check more_left/more_center/more_right (lists of dicts)
            for side in ["more_left", "more_center", "more_right"]:
                val = record.get(side)
                if not isinstance(val, list):
                    continue
                for item in val:
                    if not isinstance(item, dict):
                        continue
                    source = item.get("source", "")
                    link = item.get("link", "")
                    if source in SOURCE_MAP and link:
                        tag = SOURCE_MAP[source]
                        if link not in seen_urls[tag]:
                            seen_urls[tag].add(link)
                            entry = {
                                "url": link,
                                "bias_side": side,
                                "source_headline": item.get("headline", ""),
                                "source_summary": item.get("content", item.get("summary", "")),
                                "source_rating": item.get("rating", ""),
                                "source_image_link": item.get("image_link", ""),
                                "news_type": item.get("news_type", ""),
                            }
                            entry.update(allsides_meta)
                            entries[tag].append(entry)

    return entries


def scrape_source(tag, entries, output_file, make_session_fn, parse_fn):
    """Scrape all URLs for a given source and write results to output file."""
    total = len(entries)
    if total == 0:
        print(f"  No URLs found for {tag}, skipping.")
        return

    print(f"\n{'='*60}")
    print(f"  Scraping {tag}: {total} URLs")
    print(f"  Output: {output_file}")
    print(f"{'='*60}")

    session = make_session_fn()
    success = 0
    failed = 0
    no_body = 0

    # Open file in append mode to allow resumption
    # But first check what we already have
    already_done = set()
    if os.path.exists(output_file):
        with open(output_file, "r") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    already_done.add(rec.get("url", ""))
                except Exception:
                    pass
        if already_done:
            print(f"  Found {len(already_done)} already-scraped URLs, will skip them.")

    remaining = [e for e in entries if e["url"] not in already_done]
    print(f"  Remaining to scrape: {len(remaining)}")

    with open(output_file, "a") as f:
        for i, entry in enumerate(remaining):
            url = entry["url"]
            print(f"  [{i+1}/{len(remaining)}] {url[:80]}...", end=" ", flush=True)

            try:
                details = parse_fn(session, url)

                record = {
                    "source": tag,
                    "url": url,
                    "scraped_title": details.get("title", ""),
                    "scraped_subtitle": details.get("subtitle", ""),
                    "scraped_author": details.get("author", ""),
                    "scraped_date": details.get("date", ""),
                    "scraped_date_iso": details.get("date_iso", ""),
                    "scraped_body": details.get("body", ""),
                    "scraped_image": details.get("image", ""),
                    "scraped_category": details.get("category", ""),
                    "scraped_tags": details.get("tags", []),
                    # AllSides metadata
                    "allsides_headline": entry["allsides_headline"],
                    "allsides_topic": entry["allsides_topic"],
                    "allsides_tags": entry["allsides_tags"],
                    "allsides_date": entry["allsides_date"],
                    "allsides_headline_link": entry["allsides_headline_link"],
                    "bias_side": entry["bias_side"],
                    "source_headline": entry["source_headline"],
                    "source_summary": entry["source_summary"],
                    "source_rating": entry["source_rating"],
                    "source_image_link": entry["source_image_link"],
                    "news_type": entry["news_type"],
                    "scraped_at": datetime.utcnow().isoformat() + "Z",
                }

                json.dump(record, f)
                f.write("\n")
                f.flush()

                if details.get("body"):
                    success += 1
                    print("OK")
                else:
                    no_body += 1
                    print("(no body)")

            except Exception as e:
                failed += 1
                print(f"FAILED: {e}")
                # Write a minimal record so we don't retry on resume
                error_record = {
                    "source": tag,
                    "url": url,
                    "scraped_title": "",
                    "scraped_body": "",
                    "error": str(e),
                    "allsides_headline": entry["allsides_headline"],
                    "allsides_topic": entry["allsides_topic"],
                    "allsides_tags": entry["allsides_tags"],
                    "allsides_date": entry["allsides_date"],
                    "allsides_headline_link": entry["allsides_headline_link"],
                    "bias_side": entry["bias_side"],
                    "source_headline": entry["source_headline"],
                    "source_summary": entry["source_summary"],
                    "source_rating": entry["source_rating"],
                    "news_type": entry["news_type"],
                    "scraped_at": datetime.utcnow().isoformat() + "Z",
                }
                json.dump(error_record, f)
                f.write("\n")
                f.flush()

            delay = random.uniform(DELAY_MIN, DELAY_MAX)
            time.sleep(delay)

    total_done = len(remaining)
    print(f"\n  {tag} complete: {total_done} URLs processed")
    print(f"    With body: {success}")
    print(f"    No body:   {no_body}")
    print(f"    Failed:    {failed}")


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print(f"Reading AllSides data from: {ALLSIDES_FILE}")
    if not os.path.exists(ALLSIDES_FILE):
        print(f"ERROR: File not found: {ALLSIDES_FILE}")
        sys.exit(1)

    entries = extract_entries(ALLSIDES_FILE)
    print(f"\nURLs found:")
    print(f"  Fox News Digital:      {len(entries['foxnews'])}")
    print(f"  New York Times (News): {len(entries['nytimes'])}")
    print(f"  The Hill:              {len(entries['thehill'])}")
    print(f"  Total:                 {sum(len(v) for v in entries.values())}")

    # Scrape each source
    scrape_source("foxnews", entries["foxnews"], FOX_OUTPUT, fox_session, fox_parse)
    scrape_source("thehill", entries["thehill"], HILL_OUTPUT, hill_session, hill_parse)
    scrape_source("nytimes", entries["nytimes"], NYT_OUTPUT, nyt_session, nyt_parse)

    print(f"\n{'='*60}")
    print("All done!")
    print(f"  Fox News output:  {FOX_OUTPUT}")
    print(f"  The Hill output:  {HILL_OUTPUT}")
    print(f"  NYTimes output:   {NYT_OUTPUT}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
