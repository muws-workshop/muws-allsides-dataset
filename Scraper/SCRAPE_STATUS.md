# Scrape Status — AllSides Featured-Article Corpus

**Last updated:** 2026-06-10
**Pipeline:** `media_pipeline.py`
**Output:** `crawled_articles_corpus.json`

---

## Overall Progress

| Metric | Count | % |
|---|---|---|
| In-scope article slots | 2,549 | 100% |
| **SUCCESS** | **2,140** | **83.9%** |
| FAILED_PAYWALL (IP block) | 395 | 15.5% |
| FAILED_PARSE (video pages) | 12 | 0.5% |
| FAILED_HTTP_404 (deleted) | 2 | 0.1% |

---

## Per-Domain Breakdown

| Domain | Stance | Success | Total | Rate | Notes |
|---|---|---|---|---|---|
| apnews.com | Left | 194 | 194 | **100%** | Done |
| cnn.com | Left | 163 | 163 | **100%** | Done |
| newsweek.com | Center | 281 | 281 | **100%** | Done |
| washingtonexaminer.com | Right | 179 | 179 | **100%** | Done |
| nypost.com | Right | 324 | 325 | **99.7%** | 1 deleted page (404) |
| thehill.com | Center | 372 | 373 | **99.7%** | 1 video page (no article text) |
| foxnews.com | Right | 486 | 498 | **97.6%** | 11 video pages + 1 deleted |
| reuters.com | Center | 121 | 271 | **44.6%** | 150 IP-blocked (401) |
| nytimes.com | Left | 20 | 265 | **7.5%** | 245 IP-blocked (403) |

---

## What's Blocking the Remaining 395

Both NYT and Reuters have **temporarily IP-blocked this machine** after the bulk scrape run. This is a rate-limit ban, not a permanent block -- it lifts after hours to a day.

- **nytimes.com (245):** Returns HTTP 403. NYT detects automated traffic and blocks the IP. The Google-referer trick works when not blocked.
- **reuters.com (150):** Returns HTTP 401. Reuters uses a server-side JS challenge (Cloudflare-like) that `curl_cffi` can't always solve. When the IP isn't flagged, ~20-40% of requests get through per batch.

### Permanently unfixable (14 total)

- **11 Fox News `/video/` URLs:** Video embed pages with no article text. AllSides linked to video clips, not written articles.
- **1 Fox News 404:** Article page has been deleted.
- **1 NY Post 404:** Article page has been deleted.
- **1 The Hill video page:** Video embed, only a 643-char description exists.

---

## How to Recover the Remaining Articles

### Step 1: Check if the blocks have lifted

```bash
python check_unblock.py
```

This probes one URL per domain. When it says `UNBLOCKED`, proceed to step 2.

### Step 2: Run patch mode

```bash
# Patch one domain at a time (less aggressive, less likely to re-trigger the block)
python media_pipeline.py --mode patch --domain reuters.com
python media_pipeline.py --mode patch --domain nytimes.com
```

Each patch run recovers ~20-40% of remaining failures (the IP block is intermittent -- some requests slip through). The pipeline uses:
- Fresh session per request (different TLS fingerprint each time)
- Randomized 1.5-4s delay between requests
- `trafilatura` as a fallback parser when the HTML is fetched successfully

### Step 3: Check progress

```bash
python media_pipeline.py --mode audit
```

### Step 4: Repeat

Space out patch runs by a few hours. After 3-4 runs over 1-2 days, expect:
- **Reuters:** 80-90% (up from 44.6%)
- **NYT:** 40-60% (up from 7.5%)

NYT is the hardest target due to its aggressive bot detection + hard paywall. Full recovery is unlikely without a NYT subscription or proxy rotation.

---

## Files

| File | Description |
|---|---|
| `media_pipeline.py` | Main pipeline script (scrape / patch / audit modes) |
| `check_unblock.py` | Quick probe to check if IP blocks have lifted |
| `crawled_articles_corpus.json` | Master output (2,549 article slots) |
| `PIPELINE_SPEC.md` | Build specification and architecture docs |
| `output_2025_2026/allsides_jan2025_may2026_combined.jsonl` | Input corpus (1,919 AllSides stories) |
