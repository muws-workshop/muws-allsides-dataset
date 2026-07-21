"""
Shared constants, styling, and cached data loaders for the dataset explorer pages.
"""

import base64
import html
import json
import os
from io import BytesIO

import pandas as pd
import streamlit as st

# CONFIGURATION
STANCES = ["left", "center", "right"]
STANCE_LABEL = {"left": "Left", "center": "Center", "right": "Right"}

BIAS_COLOR = {
    "left": "#1c5cab",
    "lean left": "#86b6ef",
    "center": "#9866a1",
    "lean right": "#e58f83",
    "right": "#c0392b",
}
BIAS_SHORT = {"left": "L", "lean left": "LL", "center": "C", "lean right": "LR", "right": "R"}
BIAS_TEXT = {
    "left": "#ffffff", "lean left": "#0b0b0b", "center": "#ffffff",
    "lean right": "#0b0b0b", "right": "#ffffff",
}
STANCE_COLOR = {"left": BIAS_COLOR["left"], "center": BIAS_COLOR["center"], "right": BIAS_COLOR["right"]}

# Chart chrome (light surface)
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
SEQ_BLUE = "#2a78d6"

PLOTLY_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family='system-ui, -apple-system, "Segoe UI", sans-serif', color=INK_MUTED, size=12),
    margin=dict(l=8, r=8, t=28, b=8),
)
PLOTLY_CONFIG = {"displayModeBar": False}


def tint(hex_color: str, f: float) -> str:
    """Mix a hex color toward white by factor f in [0, 1]."""
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    mix = lambda c: round(c + (255 - c) * f)
    return f"#{mix(r):02x}{mix(g):02x}{mix(b):02x}"


def bias_chip(rating: str) -> str:
    r = (rating or "").lower()
    bg = BIAS_COLOR.get(r, "#c3c2b7")
    fg = BIAS_TEXT.get(r, "#0b0b0b")
    return (f'<span class="bias-chip" style="background:{bg};color:{fg};" '
            f'title="{html.escape(rating or "unrated")}">{BIAS_SHORT.get(r, "?")}</span>')


# DATA LOADING
@st.cache_resource(show_spinner="Loading AllSides stories …")
def load_stories(path: str) -> pd.DataFrame:
    """One row per AllSides story, keyed by URL slug."""
    rows, seen = [], set()
    with open(path) as f:
        for line in f:
            s = json.loads(line)
            slug = s.get("headline_link", "").rstrip("/").split("/")[-1]
            if not slug or slug in seen:
                continue
            seen.add(slug)
            rows.append({
                "slug": slug,
                "date": s.get("date") or None,
                "headline": s.get("headline", ""),
                "summary": s.get("summary", ""),
                "topic": s.get("topic", ""),
                "tags": s.get("tags") or [],
                "allsides_link": s.get("headline_link", ""),
                **{f"stance_{k}": s.get(k) if isinstance(s.get(k), dict) else None for k in STANCES},
            })
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    return df


@st.cache_resource(show_spinner="Loading crawled articles …")
def load_crawled(data_dir: str):
    """Returns ({(slug, stance): article}, articles DataFrame)."""
    crawled: dict[tuple, dict] = {}
    rows: list[dict] = []
    for fname in sorted(os.listdir(data_dir)):
        if not fname.endswith(".json"):
            continue
        with open(os.path.join(data_dir, fname)) as f:
            data = json.load(f)
        for slug, stances in data.items():
            for stance_key, article in stances.items():
                article = {**article, "source_file": fname}
                crawled[(slug, stance_key)] = article
                imgs = article.get("extracted_images") or []
                rows.append({
                    "slug": slug,
                    "stance_key": stance_key,
                    "domain": article.get("domain", ""),
                    "rating": article.get("rating", ""),
                    "has_local_image": any(isinstance(i, dict) and i.get("local_path") for i in imgs),
                    "text_length": len(article.get("extracted_body_text") or ""),
                })
    return crawled, pd.DataFrame(rows)


@st.cache_data(show_spinner=False, max_entries=600)
def thumb_data_uri(abs_path: str, width: int = 480) -> str | None:
    """Small base64 JPEG thumbnail so card HTML can embed local images."""
    try:
        from PIL import Image
        img = Image.open(abs_path).convert("RGB")
        if img.width > width:
            img = img.resize((width, max(1, int(img.height * width / img.width))))
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=80)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


@st.cache_resource(show_spinner=False)
def build_story_index(stories_path: str, data_dir: str) -> pd.DataFrame:
    """Story df enriched with crawl coverage columns."""
    stories = load_stories(stories_path).copy()
    crawled, articles = load_crawled(data_dir)

    per_slug = articles.groupby("slug") if not articles.empty else None
    n_crawled, has_img, domains = {}, {}, {}
    if per_slug is not None:
        for slug, grp in per_slug:
            n_crawled[slug] = len(grp)
            has_img[slug] = bool(grp["has_local_image"].any())
            domains[slug] = sorted(grp["domain"].unique())

    stories["n_crawled"] = stories["slug"].map(n_crawled).fillna(0).astype(int)
    stories["has_images"] = stories["slug"].map(has_img).fillna(False).astype(bool)
    stories["crawled_domains"] = stories["slug"].map(domains).apply(lambda v: v if isinstance(v, list) else [])
    return stories