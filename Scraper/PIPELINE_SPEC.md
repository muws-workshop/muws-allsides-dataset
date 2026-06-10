# Build Spec: AllSides Featured-Article Scraper (`media_pipeline.py`)

**Audience:** Claude Code (implementation agent)
**Goal:** Build a single-file Python pipeline that scrapes the full article text behind the **top-tier featured** Left / Center / Right links in an AllSides dataset, using **custom per-domain scrapers** for the 9 highest-volume domains only.

---

## 0. Context & Scope

- Input is a JSONL file (`allsides_jan2025_may2026_combined.jsonl`), one AllSides **story** per line.
- We already have all the article *links + metadata*. This pipeline fetches the **full body text** behind those links.
- We are crawling the **actual news websites directly** (no GDELT/RSS path — that route is out of scope).
- We only care about the three **top-level featured objects** per story: `left`, `center`, `right`. **Ignore** the `more_left` / `more_center` / `more_right` arrays entirely.
- **Only 9 custom-scraper domains are in scope.** Any link whose domain is not one of the 9 is **skipped entirely** — no generic/trafilatura fallback, no record written.

### The 9 target domains (top 3 per stance, per verified corpus analysis)

| Stance | Rank 1 | Rank 2 | Rank 3 |
|---|---|---|---|
| Left | `nytimes.com` | `apnews.com` | `cnn.com` |
| Center | `thehill.com` | `newsweek.com` | `reuters.com` |
| Right | `foxnews.com` | `nypost.com` | `washingtonexaminer.com` |

> Note: `washingtonexaminer.com` is the Right #3 (the original spec only named Fox + NY Post; analysis confirmed the third).

### Build order (incremental — build & verify ONE domain before the next)

1. `nytimes.com`, `thehill.com`, `foxnews.com` (top-1 of each stance)
2. `apnews.com`, `newsweek.com`, `nypost.com` (top-2)
3. `cnn.com`, `reuters.com`, `washingtonexaminer.com` (top-3)

---

## 1. Input Schema (what each JSONL line looks like)

Each line is one story. Relevant fields:

```jsonc
{
  "headline_link": "https://www.allsides.com/story/<slug>",  // use slug as story_id
  "date": "2025-05-14",
  "headline": "....",
  "left":   { "source": "...", "headline": "...", "link": "https://...", "rating": "lean left", ... },
  "center": { "source": "...", "headline": "...", "link": "https://...", "rating": "center", ... },
  "right":  { "source": "...", "headline": "...", "link": "https://...", "rating": "right", ... }
  // more_left / more_center / more_right arrays exist but are IGNORED
}
```

- A slot (`left`/`center`/`right`) may occasionally be missing or have an empty `link` — handle gracefully (skip that slot, don't crash).
- `rating` is granular ("left", "lean left", "center", "lean right", "right"). The **slot name** is the routing stance; **`rating`** is the recorded stance. Store both.

### `story_id` derivation

- Primary: the slug from `headline_link` (the path segment after `/story/`).
- Fallback (only if `headline_link` missing/malformed): `sha1(date + "|" + headline)[:16]`.

---

## 2. Output Schema (`crawled_articles_corpus.json`)

Single master JSON file, keyed by `story_id`, each with up to three stance sub-objects. **This exact structure:**

```jsonc
{
  "<story_id>": {
    "left": {
      "domain": "nytimes.com",
      "url": "https://www.nytimes.com/.../article.html",
      "rating": "lean left",
      "scrape_timestamp": "2026-06-10T03:30:00Z",
      "http_status_code": 200,
      "execution_status": "SUCCESS",
      "extracted_headline": "....",
      "extracted_body_text": "Full clean body text...",
      "error_payload": null
    },
    "center": { ... },
    "right":  { ... }
  }
}
```

- `execution_status` enum: `SUCCESS`, `FAILED_PAYWALL`, `FAILED_TIMEOUT`, `FAILED_HTTP_<code>`, `FAILED_PARSE`, `FAILED_NETWORK`.
- `scrape_timestamp`: UTC ISO-8601 with `Z`.
- On any failure: `extracted_headline` / `extracted_body_text` = `""`, `error_payload` = human-readable reason.
- Slots whose domain is not one of the 9 are **not written at all** (skipped, not recorded as a failure).

---

## 3. Architecture

Implement as **Strategy B (group-by-domain, batched)** with the dispatcher logic living inside the bucketing step.

### 3.1 Index pass
Read the JSONL once. For each story, for each of the 3 slots:
- derive `story_id`, parse `domain` from the link (`urlparse().netloc`, strip leading `www.`),
- if domain is one of the 9, append `(story_id, slot, url, source, rating)` to that domain's bucket.

Result: an in-memory dict `{domain: [work_items...]}` — the "list of links per scraper."

### 3.2 Bucket selection (CLI-driven)
- no flag → all 9 buckets
- `--domain foxnews.com` → only that bucket
- `--stance right` → only links sitting in the right slot (filter across buckets)
- `--domain` + `--stance` → intersection

### 3.3 Per-bucket run
For each work item in the selected bucket(s):
1. **Resume check** — if master JSON already has `story_id → slot` with `execution_status == "SUCCESS"`, skip immediately (no network).
2. **Fetch** via the shared network layer.
3. **Route** to that domain's custom parser.
4. **Write** the result into the master JSON under `story_id → slot`. Persist incrementally (see 3.6).

### 3.4 Shared network layer
- `requests.Session`.
- Rotating `User-Agent` from a small pool of real browser UAs; browser-like headers (`Accept`, `Accept-Language`, `Referer`, etc.).
- Backoff between requests: `time.sleep(random.uniform(1.5, 4.0))`.
- Timeout per request (e.g. 20s). On timeout → `FAILED_TIMEOUT`.
- Classify response: 200 → parse; 401/403 or Cloudflare challenge markers → `FAILED_PAYWALL`; other 4xx/5xx → `FAILED_HTTP_<code>`; connection error → `FAILED_NETWORK`.

### 3.5 Custom parsers (one function per domain)
Signature: `parse(html, url) -> (headline: str, body_text: str)`.
Each parser uses a **fallback chain** so it degrades gracefully:
1. site-specific selectors (the main article container; strip nav, ads, related-links, newsletter/cookie banners, captions, scripts),
2. then generic `<article>` / main-content extraction,
3. then JSON-LD (`<script type="application/ld+json">` → `articleBody` / `headline`).
If all fail → raise/return empty so caller marks `FAILED_PARSE`.

> **Selectors must be verified against live pages by the implementer.** Site markup drifts and cannot be assumed. Keep each domain's selectors in a clearly-labeled constant at the top of its parser so they're trivial to adjust. Add a `--debug` flag that prints, per URL, which fallback tier fired and the extracted char count.

### 3.6 State / persistence
- Load existing master JSON at startup (if present) so resume works across runs.
- Write back safely: write to a temp file then atomic `os.replace` (avoid corrupting the master on interrupt). Flush periodically (e.g. every N items) and on exit.

---

## 4. CLI Modes

`python media_pipeline.py --mode <mode> [filters]`

### `--mode scrape` (default)
Runs the index → bucket-select → per-bucket scrape flow above.
Filters: `--domain <domain>`, `--stance <left|center|right>`, `--limit <n>` (handy for testing), `--debug`.
Also: `--input <path>` (JSONL) and `--output <path>` (master JSON), with sensible defaults.

### `--mode patch`
In-place remediation. Scan master JSON, select records matching an optional `--domain` where `execution_status != "SUCCESS"` **OR** `len(extracted_body_text) == 0`, pull just those into a queue, re-run the (corrected) fetch+parse, and patch repaired payloads back in — **without** re-running or mutating existing `SUCCESS` records.

### `--mode audit`
Print a readable Markdown diagnostic report to stdout (scrape nothing):
1. **Completeness matrix** — successful extractions vs expected in-scope slots (count of in-scope slots = total links across the 9 buckets; report overall and per domain & per stance).
2. **Failure analysis** — table of failure categories (HTTP codes, timeouts, paywalls, parse fails) broken down by domain.
3. **Low-quality flags** — list every record marked `SUCCESS` whose `extracted_body_text` is **< 200 chars** (likely captured a cookie/paywall/disclaimer instead of article text).

---

## 5. Acceptance Criteria

- Runs on the user's own machine; `requests` + `beautifulsoup4` only (no headless browser required for v1).
- `--mode scrape --domain nytimes.com --limit 5 --debug` fetches 5 NYT articles and shows extraction tiers.
- Re-running the same command skips the 5 already-SUCCESS records (resume works).
- Master JSON validates against the schema in §2.
- `--mode audit` produces all three report sections.
- Non-target domains never appear in the master JSON.
- Interrupting mid-run never corrupts the master JSON.
