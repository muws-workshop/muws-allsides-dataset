"""
AllSides crawler.
=================
Scrapes the AllSides balanced-news feed into a jsonl, complete in one run:

- walks the listing pages, then every story page (Cloudflare-bypassing session
  with retry + backoff)
- extracts the FULL story summary from the current page markup (the
  `div.editor-content` that matches the meta description — never the
  truncated ~160-char SEO meta tag alone)
- parses the featured Left/Center/Right articles (source, headline, rating,
  excerpt, external link) and the more_left/center/right lists
- downloads every featured stance image to  images/<story-slug>/<stance>/
  next to the output jsonl and records it as `image_local_path`

Usage:
    python allsides_crawler.py [--start 2026-06-01] [--end 2026-12-31]
                               [--workers 6] [--delay 0.5] [--max-pages 100]
                               [--limit N] [--no-more] [--no-images]
                               [--output PATH]

Output defaults to  output/allsides_crawl_<start>_<end>.jsonl ; the canonical
dataset is  output/allsides_jan2025_may2026.jsonl . Image downloads are
resumable (files already on disk are skipped).

Requires: curl_cffi, beautifulsoup4.
"""

import argparse
import json
import os
import re
import shutil
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

from bs4 import BeautifulSoup
from curl_cffi import requests

# ── Configuration ────────────────────────────────────────────────────────────

BASE = "https://www.allsides.com"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"

STANCES = ["left", "center", "right"]
MAX_FILENAME_LEN = 120
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"}

TAG_PREFIXES = [
    ("Fact Check", "FACT CHECK"),
    ("Analysis", "ANALYSIS"),
    ("Opinion", "OPINION"),
    ("News", "NEWS"),
]
OPEN_ON_RE = re.compile(r"(Open on .+?)(?:Possible Paywall)?$")


# ── HTTP ─────────────────────────────────────────────────────────────────────

def make_session():
    return requests.Session(impersonate="chrome")


def get_with_retry(session, url: str, attempts: int = 3, delay: float = 0.0):
    """GET with backoff; raises on final failure so a failed fetch is never
    mistaken for an empty page."""
    last_err = None
    for a in range(attempts):
        try:
            r = session.get(url, timeout=45)
            if delay:
                time.sleep(delay)
            if r.status_code == 200:
                return r
            last_err = RuntimeError(f"HTTP {r.status_code}")
        except Exception as e:
            last_err = e
        time.sleep(5 * (a + 1))
    raise last_err


def fetch_soup(session, url: str, delay: float = 0.0) -> BeautifulSoup:
    return BeautifulSoup(get_with_retry(session, url, delay=delay).text, "html.parser")


# ── Parsing ──────────────────────────────────────────────────────────────────

def is_truncated(summary: str) -> bool:
    return bool(summary) and summary.rstrip().endswith("...")


def norm_ws(t: str) -> str:
    return re.sub(r"\s+", "", t)


def parse_date(raw: str) -> str:
    cleaned = re.sub(r"^.*?•\s*", "", raw)
    cleaned = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", cleaned).strip()
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


def extract_story_summary(soup) -> str:
    """Full story description from the current markup.

    The description lives in a `div.editor-content`; when several exist, the
    right one starts with the same text as the SEO meta description. Falls
    back to the legacy selector, then to the (truncated) meta tag.
    """
    meta = soup.find("meta", attrs={"name": "description"})
    meta_txt = meta.get("content", "").strip() if meta else ""
    divs = soup.find_all("div", class_="editor-content")
    if divs:
        key = norm_ws(meta_txt[:80].removesuffix("...")) if meta_txt else ""
        for div in divs:
            text = re.sub(r"\s+", " ", div.get_text(" ", strip=True))
            if not key or norm_ws(text).startswith(key):
                if len(text) >= len(meta_txt):
                    return text
    legacy = soup.find("div", class_=lambda c: c and "story-id-page-description" in c)
    if legacy:
        return legacy.get_text(strip=True)
    return meta_txt


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
    news_type_el = item.find(class_=lambda c: c and "news-type" in str(c))
    news_type = news_type_el.get_text(strip=True) if news_type_el else ""
    return {
        "headline": headline, "source": source, "allsides_link": allsides_link,
        "rating_img": rating_img, "rating": parse_bias(rating_img),
        "news_type": news_type,
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
        stance_cls = [c for c in child.get("class", []) if c in STANCES]
        if not stance_cls or stance_cls[0] in featured:
            continue
        stance = stance_cls[0]

        headline = ext_link = source = rating_img_url = image_link = summary = ""
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


def fetch_article_details(session, allsides_link: str, delay: float = 0.0) -> dict:
    result = {"link": "", "summary": "", "content": "", "image_link": ""}
    if not allsides_link:
        return result
    try:
        soup = fetch_soup(session, allsides_link, delay=delay)
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


# ── Image download ───────────────────────────────────────────────────────────

def sanitize(name: str) -> str:
    return re.sub(r"[^\w\-.]", "_", name)[:MAX_FILENAME_LEN]


def filename_from_url(url: str) -> str:
    basename = os.path.basename(unquote(urlparse(url).path)) or "image"
    basename = sanitize(basename)
    if not re.search(r"\.(jpg|jpeg|png|gif|webp|svg|bmp|avif)$", basename, re.I):
        basename += ".jpg"
    return f"000_{basename}"


def download_image(url: str, dest: Path) -> bool:
    """Skips files already on disk, so crawls are image-resumable."""
    if dest.is_file() and dest.stat().st_size >= 100:
        return True
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=45) as r:
            ctype = r.headers.get("Content-Type", "")
            if not (ctype.startswith("image/") or "octet-stream" in ctype):
                return False
            data = r.read()
        if len(data) < 100:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return True
    except Exception:
        return False


# ── Story processing ─────────────────────────────────────────────────────────

def slug_of(story: dict) -> str:
    return story.get("headline_link", "").rstrip("/").split("/")[-1]


def process_story(story: dict, args, images_dir: Path) -> dict | None:
    session = make_session()
    soup = fetch_soup(session, story["headline_link"], delay=args.delay)

    date_el = soup.find("p", class_=lambda c: c and "tracking-wide" in c) \
        or soup.find("p", class_=lambda c: c and "text-gray-500" in c)
    story["date"] = parse_date(date_el.get_text(strip=True)) if date_el else ""
    if not story["date"] or not (args.start <= story["date"] <= args.end):
        return None

    topic_el = soup.find("a", href=lambda h: h and "/topics/" in h)
    story["topic"] = (topic_el.get_text(strip=True).replace("News and Information about ", "")
                      if topic_el else "")
    story["topic_link"] = (BASE + topic_el["href"]) if topic_el else ""
    story["tags"] = [t.get_text(strip=True).rstrip(",").strip()
                     for t in soup.find_all("a", href=lambda h: h and "/tags/" in h)]
    story["summary"] = extract_story_summary(soup)

    featured = parse_featured_from_container(soup)

    items = soup.find_all("div", class_=lambda c: c and "news-item" in c)
    more_articles = {s: [] for s in STANCES}
    seen_headlines = {featured[s]["headline"] for s in featured if featured[s]["headline"]}
    for item in items:
        classes = item.get("class", [])
        for stance in STANCES:
            if stance in classes:
                art = parse_article_from_item(item)
                if art["headline"] and art["headline"] not in seen_headlines:
                    seen_headlines.add(art["headline"])
                    more_articles[stance].append(art)
                break

    for stance in STANCES:
        if stance not in featured and more_articles[stance]:
            first = more_articles[stance].pop(0)
            featured[stance] = {
                "source": first["source"], "headline": first["headline"],
                "link": "", "rating_img": first["rating_img"],
                "rating": first["rating"], "summary": "", "image_link": "",
                "news_type": first.get("news_type", ""),
                "_needs_fetch": True, "_allsides_link": first["allsides_link"],
            }

    slug = slug_of(story)
    for stance in STANCES:
        if stance in featured:
            feat = featured[stance]
            if feat.get("_needs_fetch"):
                details = fetch_article_details(session, feat.pop("_allsides_link"),
                                                delay=args.delay)
                feat.pop("_needs_fetch", None)
                feat["link"] = details["link"]
                feat["summary"] = details["summary"]
                feat["image_link"] = details["image_link"]

            # fetch the stance picture right away
            url = feat.get("image_link", "")
            if not args.no_images and url.startswith("http"):
                dest = images_dir / slug / stance / filename_from_url(url)
                if download_image(url, dest):
                    feat["image_local_path"] = str(dest.relative_to(images_dir.parent))
            story[stance] = feat
        else:
            story[stance] = ""

        more = []
        if not args.no_more:
            for art_raw in more_articles.get(stance, []):
                details = fetch_article_details(session, art_raw["allsides_link"],
                                                delay=args.delay)
                more.append({
                    "source": art_raw["source"], "headline": art_raw["headline"],
                    "link": details["link"], "rating_img": art_raw["rating_img"],
                    "rating": art_raw["rating"], "image_link": details["image_link"],
                    "news_type": art_raw.get("news_type", ""),
                    "allsides_link": art_raw["allsides_link"], "content": details["content"],
                })
        story[f"more_{stance}"] = more
    return story


def clean_stance_summaries(records: list[dict]) -> dict:
    """Strip 'Open on <Source>' suffixes and tag prefixes from stance excerpts."""
    stats = {"tag_extracted": 0, "open_on_extracted": 0}
    for rec in records:
        for stance in STANCES:
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
                stats["open_on_extracted"] += 1

            for prefix, nt_value in TAG_PREFIXES:
                if summary.startswith(prefix) and len(summary) > len(prefix):
                    nxt = summary[len(prefix)]
                    if nxt.isupper() or nxt == " ":
                        summary = summary[len(prefix):].lstrip()
                        news_type = news_type or nt_value
                        stats["tag_extracted"] += 1
                        break

            entry["summary"] = summary
            if news_type:
                entry["news_type"] = news_type
            if open_on_source:
                entry["open_on_source"] = open_on_source
    return stats


# ── Main ─────────────────────────────────────────────────────────────────────

def collect_story_urls(max_pages: int) -> list[dict]:
    session = make_session()
    all_stories, seen = [], set()
    for page in range(1, max_pages + 1):
        try:
            soup = fetch_soup(session, f"{BASE}/recent-headline-roundups?page={page}", delay=0.3)
        except Exception:
            break
        cards = soup.find_all("div", class_=lambda c: c and "clearfix" in c and "border-b" in c)
        if not cards:
            break
        new = 0
        for card in cards:
            h2 = card.find("h2")
            link_el = h2.find("a") if h2 else None
            if not link_el:
                continue
            href = link_el.get("href", "")
            if href in seen:
                continue
            seen.add(href)
            new += 1
            all_stories.append({
                "headline": link_el.get_text(strip=True),
                "headline_link": BASE + href if href.startswith("/") else href,
            })
        print(f"  listing page {page}: {len(all_stories)} stories so far", flush=True)
        if new == 0:
            break
    return all_stories


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="allsides_crawler",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--start", default="2025-01-01", help="earliest story date (YYYY-MM-DD)")
    ap.add_argument("--end", default="2026-12-31", help="latest story date (YYYY-MM-DD)")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--delay", type=float, default=0.5, help="seconds between requests")
    ap.add_argument("--max-pages", type=int, default=100, help="listing pages to walk")
    ap.add_argument("--limit", type=int, default=0, help="max stories (0 = all; for testing)")
    ap.add_argument("--no-more", action="store_true",
                    help="skip the more_left/center/right article lists (much faster)")
    ap.add_argument("--no-images", action="store_true", help="skip image downloads")
    ap.add_argument("--output", default="", help=f"output jsonl (default: "
                    f"{OUTPUT_DIR}/allsides_crawl_<start>_<end>.jsonl)")
    args = ap.parse_args()

    output_path = Path(args.output) if args.output else \
        OUTPUT_DIR / f"allsides_crawl_{args.start}_{args.end}.jsonl"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    images_dir = output_path.parent / "images"

    print(f"AllSides crawl {args.start} → {args.end}  |  workers={args.workers}")
    print(f"Output: {output_path}\nImages: {images_dir}")

    stories = collect_story_urls(args.max_pages)
    if args.limit:
        stories = stories[: args.limit]
    print(f"{len(stories)} stories to process.")

    results, errors, skipped = [], 0, 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(process_story, s, args, images_dir): s for s in stories}
        for i, fut in enumerate(as_completed(futures), 1):
            try:
                res = fut.result()
                if res is not None:
                    results.append(res)
                else:
                    skipped += 1
            except Exception as e:
                errors += 1
                print(f"  ERROR {futures[fut]['headline_link']}: {e}", file=sys.stderr)
            if i % 25 == 0:
                print(f"  {i}/{len(stories)} processed "
                      f"(kept {len(results)}, out-of-range {skipped}, errors {errors})",
                      flush=True)

    results.sort(key=lambda s: s.get("date", ""), reverse=True)
    clean = clean_stance_summaries(results)

    if output_path.is_file():
        shutil.copyfile(output_path, output_path.with_suffix(".jsonl.bak"))
    with open(output_path, "w") as f:
        for story in results:
            f.write(json.dumps(story, ensure_ascii=False) + "\n")

    n_trunc = sum(is_truncated(s.get("summary", "")) for s in results)
    n_imgs = sum(1 for s in results for st in STANCES
                 if isinstance(s.get(st), dict) and s[st].get("image_local_path"))
    print(f"\nDone. {len(results)} stories → {output_path}")
    print(f"  out-of-range: {skipped} | errors: {errors}")
    print(f"  truncated summaries: {n_trunc}")
    print(f"  stance images downloaded: {n_imgs}")
    print(f"  cleaned: {clean['tag_extracted']} tag prefixes, "
          f"{clean['open_on_extracted']} 'Open on' suffixes")


if __name__ == "__main__":
    main()
