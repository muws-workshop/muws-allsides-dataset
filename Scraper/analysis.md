# Phase 1: Corpus Analysis Results

**Dataset:** AllSides Featured Coverage, January 2025 – May 2026  
**Source file:** `allsides_jan2025_may2026_combined.jsonl`  
**Scope:** Only top-tier `left`, `center`, `right` featured objects per story (ignoring `more_left`, `more_center`, `more_right`)

---

## Dataset Overview

| Metric | Value |
|---|---|
| Total stories | 1,919 |
| Stories with all 3 featured sources | 1,918 |
| Total featured article slots | 5,756 |
| Unique domains | 278 |

---

## 1. Overall Frequency Distribution (Top 6)

| Rank | Domain | Count | % of All Slots |
|---|---|---|---|
| 1 | foxnews.com | 498 | 8.7% |
| 2 | thehill.com | 373 | 6.5% |
| 3 | nypost.com | 325 | 5.6% |
| 4 | newsweek.com | 281 | 4.9% |
| 5 | reuters.com | 271 | 4.7% |
| 6 | bbc.com | 266 | 4.6% |

---

## 2. Stance-Specific Dominance (Top 3 per Stance)

### Left (1,919 total slots)

| Rank | Domain | Count | % of Left |
|---|---|---|---|
| 1 | nytimes.com | 259 | 13.5% |
| 2 | apnews.com | 194 | 10.1% |
| 3 | cnn.com | 163 | 8.5% |

### Center (1,919 total slots)

| Rank | Domain | Count | % of Center |
|---|---|---|---|
| 1 | thehill.com | 369 | 19.2% |
| 2 | newsweek.com | 281 | 14.6% |
| 3 | reuters.com | 271 | 14.1% |

### Right (1,918 total slots)

| Rank | Domain | Count | % of Right |
|---|---|---|---|
| 1 | foxnews.com | 496 | 25.9% |
| 2 | nypost.com | 324 | 16.9% |
| 3 | washingtonexaminer.com | 179 | 9.3% |

**Key observation:** The Right slot is the most concentrated — Fox News alone fills over a quarter of all right-featured slots. The Center slot is also fairly concentrated, with The Hill at 19.2%. The Left slot is the most distributed among its top sources.

---

## 3. Co-occurrence / Pairing Analysis

### Top 10 Domain Pairs (appearing together in the same story)

| Rank | Pair | Count |
|---|---|---|
| 1 | foxnews.com + thehill.com | 112 |
| 2 | bbc.com + foxnews.com | 90 |
| 3 | foxnews.com + newsweek.com | 76 |
| 4 | foxnews.com + reuters.com | 74 |
| 5 | apnews.com + foxnews.com | 66 |
| 6 | foxnews.com + nytimes.com | 66 |
| 7 | newsweek.com + nypost.com | 62 |
| 8 | cnn.com + foxnews.com | 54 |
| 9 | nypost.com + thehill.com | 49 |
| 10 | nytimes.com + thehill.com | 49 |

**Key observation:** Fox News appears in 8 of the top 10 pairs, reflecting its dominance in the Right slot. The most common pairing is Fox News (Right) + The Hill (Center), occurring in 112 stories.

### Top 10 Domain Triples (all 3 featured slots filled)

| Rank | Triple | Count |
|---|---|---|
| 1 | foxnews.com + nytimes.com + reuters.com | 16 |
| 2 | apnews.com + foxnews.com + thehill.com | 15 |
| 3 | cnn.com + foxnews.com + thehill.com | 15 |
| 4 | bbc.com + foxnews.com + nytimes.com | 14 |
| 5 | apnews.com + bbc.com + foxnews.com | 14 |
| 6 | bbc.com + cnn.com + foxnews.com | 11 |
| 7 | apnews.com + foxnews.com + reuters.com | 11 |
| 8 | foxnews.com + nytimes.com + thehill.com | 11 |
| 9 | foxnews.com + newsweek.com + nytimes.com | 10 |
| 10 | nypost.com + nytimes.com + thehill.com | 10 |

**Key observation:** The single most common story configuration is NYT (Left) + Reuters (Center) + Fox News (Right), appearing 16 times, followed closely by AP/CNN + The Hill + Fox News.

---

## 4. Cross-Stance Mobility

### Domains appearing in multiple stance categories

| Domain | Total | Left | Center | Right |
|---|---|---|---|---|
| foxnews.com | 498 | — | 2 | 496 |
| thehill.com | 373 | — | 369 | 4 |
| nypost.com | 325 | 1 | — | 324 |
| nytimes.com | 265 | 259 | 2 | 4 |
| wsj.com | 184 | 1 | 157 | 26 |
| washingtonpost.com | 104 | 100 | 2 | 2 |
| usatoday.com | 56 | 55 | — | 1 |
| unherd.com | 48 | — | 46 | 2 |
| dailywire.com | 38 | — | 1 | 37 |
| theatlantic.com | 21 | 19 | 1 | 1 |
| reason.com | 12 | — | 11 | 1 |
| allsides.com | 9 | 2 | 7 | — |
| the-independent.com | 6 | 5 | — | 1 |
| x.com | 4 | 1 | 1 | 2 |
| pressherald.com | 2 | 1 | 1 | — |
| thedispatch.com | 2 | — | 1 | 1 |

### Domains appearing in ALL 3 stances

| Domain | Left | Center | Right |
|---|---|---|---|
| nytimes.com | 259 | 2 | 4 |
| wsj.com | 1 | 157 | 26 |
| washingtonpost.com | 100 | 2 | 2 |
| theatlantic.com | 19 | 1 | 1 |
| x.com | 1 | 1 | 2 |

### Domains appearing in exactly 2 stances

| Domain | Stances | Breakdown |
|---|---|---|
| foxnews.com | Center, Right | center=2, right=496 |
| thehill.com | Center, Right | center=369, right=4 |
| nypost.com | Left, Right | left=1, right=324 |
| usatoday.com | Left, Right | left=55, right=1 |
| unherd.com | Center, Right | center=46, right=2 |
| dailywire.com | Center, Right | center=1, right=37 |
| reason.com | Center, Right | center=11, right=1 |
| allsides.com | Left, Center | left=2, center=7 |
| the-independent.com | Left, Right | left=5, right=1 |
| pressherald.com | Left, Center | left=1, center=1 |
| thedispatch.com | Center, Right | center=1, right=1 |

**Key observations:**
- **WSJ** is the most notable crossover: primarily Center (157) but frequently appears as Right (26), and once as Left. This suggests AllSides sees WSJ as a flexible source that can represent different perspectives depending on the article.
- **NYT** is overwhelmingly Left (259) but occasionally crosses into Center (2) or Right (4).
- **Daily Wire** is a new crossover entry compared to the Jun–May subset, appearing once as Center alongside its 37 Right appearances.
- Most domains stay firmly within their primary stance — true crossover is rare, with only 16 out of 278 domains (5.8%) appearing in more than one stance category.
