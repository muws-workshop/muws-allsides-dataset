# MUWS AllSides Dataset Crawler

A toolkit for scraping and analyzing news articles with left, center, and right stances from the [AllSides](https://allsides.com).

You can request the dataset created using this repository by filling out this form: https://forms.gle/tLJEZfJsnYhW5dYg8 

## Setup

```bash
# Clone and install (uv recommended)
uv venv && source .venv/bin/activate
uv pip install -r requirements.txt

# Or with pip
pip install -r requirements.txt
```

## Project Structure

To download and analyze the dataset, please perform the following steps.

> Note: If you have downloaded the dataset by requesting access via this [form](https://forms.gle/tLJEZfJsnYhW5dYg8), please copy the dataset in the folder `output` as follows: 
> - output/
>   - full_articles/
>   - images/
>   - allsides_jun2025_may2026_cleaned.jsonl

### 1. AllSides Crawl

Crawls headline roundups from https://allsides.com to produce a structured JSONL dataset of stories with left, center, and right stance including article links, bias ratings, and metadata.

```bash
python allsides_scraper.py
```

You can change parameters such as the time range directly in the [`allsides_scraper.py`](allsides_scraper.py).

### 2. Scrape News Articles

Scrapes the full article text behind the featured left, center, an right stances from the AllSides dataset obtained in the first step.

Each news domain `<domain>` has a dedicated scraper with custom HTML parsing that can be crawled as follows: 

```bash
# Scrape new articles
python news_scrapers/<domain>.py --mode scrape

# Retry failed entries
python news_scrapers/<domain>.py --mode patch

# Re-scrape SUCCESS entries to update images/captions
python news_scrapers/<domain>.py --mode refresh

# Print coverage report
python news_scrapers/<domain>.py --mode audit
```
Parameters can be changed in the [`news_scrapers/base.py`](news_scrapers/base.py)


## 3. Dataset Explorer UI

Browse articles, view locally downloaded images, and inspect extracted content. 

```bash
streamlit run multi_source_scrape/ui_analysis/dataset_explorer.py
```