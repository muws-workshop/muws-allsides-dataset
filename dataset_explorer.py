"""
Qbias Dataset Explorer — Streamlit App
=======================================
Story-centric explorer for the AllSides balanced-news dataset + local crawl.

Launch:  streamlit run dataset_explorer.py
"""

import base64
import html
import json
import os
import random
from io import BytesIO

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit_searchbox import st_searchbox

# ── CONFIGURATION ────────────────────────────────────────────────────────────

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_DATA_DIR = os.path.normpath(os.path.join(_SCRIPT_DIR, "..", "output", "per_domain_clean"))
_ALLSIDES_OUTPUT = os.path.normpath(os.path.join(_SCRIPT_DIR, "..", "..", "allsides_crawl", "output"))
# The repaired dataset (full summaries + local images).
_DEFAULT_STORIES_PATH = os.path.join(_ALLSIDES_OUTPUT, "allsides_jan2025_may2026.jsonl")

STANCES = ["left", "center", "right"]
STANCE_LABEL = {"left": "Left", "center": "Center", "right": "Right"}

# Diverging bias scale (blue ↔ red, neutral gray center). CVD-validated:
# worst adjacent-pair ΔE 13.3 (target ≥ 12). Lean shades are sub-3:1 on a
# light surface, so they never appear without a visible text label.
BIAS_COLOR = {
    "left": "#1c5cab",
    "lean left": "#86b6ef",
    "center": "#898781",
    "lean right": "#e58f83",
    "right": "#c0392b",
}
BIAS_SHORT = {"left": "L", "lean left": "LL", "center": "C", "lean right": "LR", "right": "R"}
BIAS_TEXT = {  # chip text color per background (lean shades are light → dark ink)
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


# ── DATA LOADING ─────────────────────────────────────────────────────────────

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


# ── PAGE CONFIG & CSS ────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Qbias Dataset Explorer",
    page_icon="📰",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
[data-testid="stMetric"] {
    background: #f0f2f6; border: 1px solid #d1d5db;
    border-radius: 8px; padding: 10px 14px;
}
[data-testid="stMetric"] label { color: #555 !important; }
[data-testid="stMetric"] [data-testid="stMetricValue"] { color: #1a1a2e !important; font-size: 1.5rem; }
.block-container { padding-top: 1.2rem; }
div[data-testid="stExpander"] details { border: 1px solid #e1e4e8; border-radius: 8px; }

.stance-card {
    border: 1px solid #d1d5db; border-radius: 10px;
    padding: 14px 16px; min-height: 215px;
    background: #fcfcfb; border-top: 4px solid var(--stance-color, #898781);
}
.stance-card.missing { opacity: 0.55; background: #f0f0ee; }
.stance-card .card-top {
    display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;
}
.stance-card .stance-name { font-weight: 700; color: var(--stance-color); font-size: 0.95rem; }
.stance-card .crawl-flag { font-size: 1rem; }
.stance-card .card-img {
    width: 100%; height: 120px; object-fit: cover;
    border-radius: 6px; margin-bottom: 8px; display: block;
}
.stance-card .src { color: #52514e; font-size: 0.82rem; margin-bottom: 6px; }
.stance-card .hl { font-weight: 600; color: #0b0b0b; font-size: 0.95rem; line-height: 1.3; margin-bottom: 6px; }
.stance-card .ex { color: #52514e; font-size: 0.82rem; line-height: 1.45; }
.bias-chip {
    display: inline-block; min-width: 20px; text-align: center;
    border-radius: 4px; padding: 0 4px; margin-right: 4px;
    font-size: 0.7rem; font-weight: 700;
}
.crawl-flag.ok { color: #0ca30c; font-weight: 800; }
.crawl-flag.nok { color: #d03b3b; font-weight: 800; }
</style>
""", unsafe_allow_html=True)


def bias_chip(rating: str) -> str:
    r = (rating or "").lower()
    bg = BIAS_COLOR.get(r, "#c3c2b7")
    fg = BIAS_TEXT.get(r, "#0b0b0b")
    return (f'<span class="bias-chip" style="background:{bg};color:{fg};" '
            f'title="{html.escape(rating or "unrated")}">{BIAS_SHORT.get(r, "?")}</span>')


# ── SIDEBAR — DATA SOURCES ───────────────────────────────────────────────────

with st.sidebar:
    st.title("📰 Qbias Explorer")
    st.caption("Data sources")
    stories_path = st.text_input("AllSides stories (.jsonl)", value=_DEFAULT_STORIES_PATH)
    data_dir = st.text_input("Crawled articles directory", value=_DEFAULT_DATA_DIR)

if not os.path.isfile(stories_path):
    st.error(f"Stories file not found: `{stories_path}`")
    st.stop()
if not os.path.isdir(data_dir):
    st.error(f"Crawl directory not found: `{data_dir}`")
    st.stop()

stories = build_story_index(stories_path, data_dir)
crawled, articles = load_crawled(data_dir)

if stories.empty:
    st.warning("No stories found.")
    st.stop()

# Dominant bias rating per publisher (for filter icons & stats)
domain_bias = (
    articles.groupby("domain")["rating"].agg(lambda s: s.mode().iloc[0]).to_dict()
    if not articles.empty else {}
)
BIAS_EMOJI = {"left": "🔵", "lean left": "🔹", "center": "⚪", "lean right": "🔸", "right": "🔴"}

# ── Persist filter state across page switches ────────────────────────────────
# Streamlit drops the state of widgets that are not rendered during a run
# (e.g. the explorer filters while an article page is open). Seeding the keys
# and re-assigning them every run keeps the selections alive.
_FILTER_DEFAULTS = {
    "flt_crawled": (0, 3),
    "flt_images": False,
    "flt_domains": [],
    "flt_domains_all": False,
    "search_summaries": False,
}
for _k, _v in _FILTER_DEFAULTS.items():
    st.session_state[_k] = st.session_state.get(_k, _v)
if "flt_dates" in st.session_state:
    st.session_state["flt_dates"] = st.session_state["flt_dates"]

# ══════════════════════════════════════════════════════════════════════════════
#  NAVIGATION
#  (a keyed radio instead of st.tabs so the active view survives reruns,
#   which is what lets "View crawled article" open its own page)
# ══════════════════════════════════════════════════════════════════════════════
PAGE_STATS = "📊 Dataset Statistics"
PAGE_EXPLORER = "🔎 Story Explorer"
nav = st.radio("View", [PAGE_STATS, PAGE_EXPLORER], horizontal=True,
               label_visibility="collapsed", key="nav")
st.divider()

# ══════════════════════════════════════════════════════════════════════════════
#  PAGE 1 — DATASET STATISTICS & DOCUMENTATION
# ══════════════════════════════════════════════════════════════════════════════
if nav == PAGE_STATS:
    n_stories = len(stories)
    n_articles = len(articles)
    n_domains = articles["domain"].nunique() if not articles.empty else 0
    _dates = stories["date"].dropna()
    dmin, dmax = _dates.min(), _dates.max()

    st.markdown(f"""
## The Qbias Dataset

**Qbias** pairs [AllSides](https://www.allsides.com) *balanced news* stories with locally
crawled full-text articles. Each AllSides story covers one news event through **three
stances** — an article from a **Left**-, **Center**-, and **Right**-leaning outlet — selected
and summarized by AllSides editors.

On top of the AllSides metadata (story headline, summary, topic, tags, per-stance headlines
and excerpts), we crawl the linked articles directly from the publishers to obtain the
**full body text and images**, enabling text-only and multimodal bias analysis.

**Pipeline**

1. Crawl the AllSides *balanced news* feed ({dmin} → {dmax}).
2. Resolve each story's three stance links to the original publisher URLs.
3. Scrape supported publishers with per-domain extractors (headline, body, images, videos).
4. Clean and store one JSON file per publisher, keyed by story slug and stance.

**Schema notes**

- A story is identified by its AllSides URL slug (e.g. `foreign-policy-…`).
- A crawled article is keyed by `(story slug, stance)`; its `rating` is the publisher's
  AllSides bias rating **at crawl time**.
- Publisher images are stored under `multi_source_scrape/output/images/<domain>/<slug>/<stance>/`.
- AllSides stance images are stored under `allsides_crawl/output/images/<slug>/<stance>/`
  and referenced by `image_local_path` (see `allsides_crawl/crawler/allsides_crawler.py`,
  whose repair passes also restored the full story summaries).
""")

    n_as_imgs = sum(
        1 for _, row in stories.iterrows() for s in STANCES
        if isinstance(row[f"stance_{s}"], dict) and row[f"stance_{s}"].get("image_local_path")
    )
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("AllSides stories", f"{n_stories:,}")
    c2.metric("Crawled articles", f"{n_articles:,}")
    c3.metric("Publishers crawled", f"{n_domains}")
    c4.metric("Stories fully crawled (3/3)", f"{(stories['n_crawled'] == 3).sum():,}")
    c5.metric("AllSides images stored", f"{n_as_imgs:,}")

    st.divider()

    ch1, ch2 = st.columns(2)

    with ch1:
        st.markdown("**Stories per month**")
        monthly = (
            pd.to_datetime(stories["date"].dropna().astype(str))
            .dt.to_period("M").value_counts().sort_index()
        )
        fig = go.Figure(go.Bar(
            x=monthly.index.astype(str), y=monthly.values,
            marker=dict(color=SEQ_BLUE, cornerradius=4),
            hovertemplate="%{x}: %{y} stories<extra></extra>",
        ))
        fig.update_layout(**PLOTLY_LAYOUT, height=260, bargap=0.35)
        fig.update_yaxes(gridcolor=GRIDLINE, zeroline=False)
        fig.update_xaxes(showgrid=False)
        st.plotly_chart(fig, width="stretch", config=PLOTLY_CONFIG)

    with ch2:
        st.markdown("**Crawl coverage per story** — how many of the 3 stances were crawled")
        cov = stories["n_crawled"].value_counts().reindex(range(4), fill_value=0)
        fig = go.Figure(go.Bar(
            x=[f"{i}/3" for i in cov.index], y=cov.values,
            marker=dict(color=SEQ_BLUE, cornerradius=4), width=0.55,
            text=[f"{v:,}" for v in cov.values], textposition="outside",
            textfont=dict(color="#52514e"),
            hovertemplate="%{x} stances crawled: %{y} stories<extra></extra>",
        ))
        fig.update_layout(**PLOTLY_LAYOUT, height=260, bargap=0.4)
        fig.update_yaxes(gridcolor=GRIDLINE, zeroline=False)
        fig.update_xaxes(showgrid=False)
        st.plotly_chart(fig, width="stretch", config=PLOTLY_CONFIG)

    st.markdown("**Crawled articles per publisher** — chip shows the publisher's dominant AllSides rating")
    per_dom = articles["domain"].value_counts().sort_values()
    fig = go.Figure(go.Bar(
        x=per_dom.values, y=per_dom.index, orientation="h",
        marker=dict(
            color=[BIAS_COLOR.get(domain_bias.get(d, ""), "#c3c2b7") for d in per_dom.index],
            cornerradius=4,
        ),
        width=0.6,
        text=[f" {v:,}" for v in per_dom.values], textposition="outside",
        textfont=dict(color="#52514e"),
        customdata=[domain_bias.get(d, "unrated") for d in per_dom.index],
        hovertemplate="%{y}: %{x} articles (%{customdata})<extra></extra>",
    ))
    fig.update_layout(**PLOTLY_LAYOUT, height=430, bargap=0.3)
    fig.update_xaxes(gridcolor=GRIDLINE, zeroline=False)
    fig.update_yaxes(showgrid=False)
    st.plotly_chart(fig, width="stretch", config=PLOTLY_CONFIG)
    legend_bits = "&nbsp;&nbsp;".join(
        f'{bias_chip(r)} <span style="color:#52514e;font-size:0.8rem;">{r.title()}</span>'
        for r in ["left", "lean left", "center", "lean right", "right"]
    )
    st.markdown(legend_bits, unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
#  PAGE 2a — ARTICLE VIEW (opens when a stance card's button is clicked)
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state.get("view_article"):
    slug, stance = st.session_state["view_article"]
    art = crawled.get((slug, stance))
    srow = stories[stories["slug"] == slug]

    back_col, ctx_col = st.columns([1, 4.2])
    with back_col:
        if st.button("← Back to Story Explorer", width="stretch"):
            del st.session_state["view_article"]
            st.rerun()

    if art is None or srow.empty:
        st.warning("Article not found — it may not be crawled.")
        st.stop()
    story = srow.iloc[0]

    with ctx_col:
        st.caption(
            f"Story: **{story['headline']}**  ·  "
            f"[View on AllSides]({story['allsides_link']})"
        )

    # Stance switcher — jump to the other crawled versions of this story
    sw_cols = st.columns(3)
    for s2, col in zip(STANCES, sw_cols):
        with col:
            if s2 == stance:
                st.button(f"● {STANCE_LABEL[s2]} — viewing", disabled=True,
                          width="stretch", key=f"sw_{s2}")
            elif (slug, s2) in crawled:
                if st.button(f"{STANCE_LABEL[s2]} ✓", width="stretch", key=f"sw_{s2}"):
                    st.session_state["view_article"] = (slug, s2)
                    st.rerun()
            else:
                st.button(f"{STANCE_LABEL[s2]} — not crawled", disabled=True,
                          width="stretch", key=f"sw_{s2}")

    st.divider()

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Domain", art.get("domain", "—"))
    m2.metric("Rating", art.get("rating", "—"))
    m3.metric("Status", art.get("execution_status", "—"))
    body = str(art.get("extracted_body_text", "") or "")
    m4.metric("Text length", f"{len(body):,} chars")

    if art.get("url"):
        st.markdown(f'🔗 **URL:** `{art["url"]}`')
    st.caption(f"Source file: `{art.get('source_file', '—')}`")

    headline = art.get("extracted_headline")
    if headline and str(headline).strip():
        st.markdown(f"## {str(headline).strip()}")

    # Full body directly on the page — no expander
    if body.strip():
        st.markdown(body.replace("$", "\\$"))
    else:
        st.warning("No body text extracted.")

    images = art.get("extracted_images") or []
    shown_any = False
    if images:
        st.divider()
        st.subheader(f"🖼️ Images ({len(images)})")
        for i, img in enumerate(images):
            if not isinstance(img, dict):
                continue
            local_path = img.get("local_path", "")
            if not local_path:
                continue
            abs_path = os.path.join(os.path.dirname(data_dir), local_path)
            if not os.path.isfile(abs_path):
                continue
            shown_any = True
            st.image(abs_path, width="stretch")
            info_parts = []
            if img.get("caption"):
                info_parts.append(f"**Caption:** {img['caption']}")
            if img.get("alt"):
                info_parts.append(f"**Alt:** {img['alt']}")
            info_parts.append(f"📁 `{local_path}`")
            st.markdown("  \n".join(info_parts))
        if not shown_any:
            st.info("No locally stored images for this article.")

    videos = art.get("extracted_videos") or []
    if videos:
        with st.expander(f"🎬 Videos ({len(videos)})"):
            for v in videos[:5]:
                st.code(v.get("url", str(v)) if isinstance(v, dict) else str(v), language=None)

    with st.expander("🔍 Raw JSON for this datapoint"):
        st.code(json.dumps(art, indent=2, default=str), language="json")

    err = art.get("error_payload")
    if err and str(err).strip() and str(err) != "None":
        with st.expander("⚠️ Error Payload"):
            st.code(str(err), language="json")

# ══════════════════════════════════════════════════════════════════════════════
#  PAGE 2b — STORY EXPLORER
# ══════════════════════════════════════════════════════════════════════════════
else:
    col_filters, col_main = st.columns([1, 3.2], gap="medium")

    # ── FILTERS ──────────────────────────────────────────────────────────────
    with col_filters:
        st.subheader("Filters")

        f_crawled = st.slider(
            "Crawled stances (min / max)", 0, 3, key="flt_crawled",
            help="How many of the story's Left/Center/Right articles were successfully crawled.",
        )
        f_images = st.checkbox(
            "Only stories with crawled images 🖼️", key="flt_images",
            help="At least one crawled article has locally stored images (for multimodal work).",
        )

        all_domains = sorted(articles["domain"].unique()) if not articles.empty else []
        st.session_state["flt_domains"] = [
            d for d in st.session_state["flt_domains"] if d in all_domains
        ]
        f_domains = st.multiselect(
            "News provider (up to 3)",
            options=all_domains,
            max_selections=3,
            key="flt_domains",
            format_func=lambda d: f"{BIAS_EMOJI.get(domain_bias.get(d, ''), '⚫')} {d}",
            help="Show only stories featuring a crawled article from these publishers.",
        )
        f_domains_all = False
        if len(f_domains) > 1:
            f_domains_all = st.checkbox(
                "Story must include ALL selected providers",
                key="flt_domains_all",
                help="Checked: every selected provider appears in the story's crawled "
                     "articles. Unchecked: at least one of them does.",
            )

        # Date range: mini timeline + slider
        st.markdown("**Date range**")
        valid_dates = stories["date"].dropna()
        dmin, dmax = valid_dates.min(), valid_dates.max()
        monthly = (
            pd.to_datetime(valid_dates.astype(str))
            .dt.to_period("M").value_counts().sort_index()
        )
        fig = go.Figure(go.Bar(
            x=monthly.index.astype(str), y=monthly.values,
            marker=dict(color=SEQ_BLUE, cornerradius=2),
            hovertemplate="%{x}: %{y} stories<extra></extra>",
        ))
        fig.update_layout(**PLOTLY_LAYOUT, height=80, bargap=0.3)
        fig.update_layout(margin=dict(l=0, r=0, t=0, b=0))
        fig.update_xaxes(visible=False)
        fig.update_yaxes(visible=False)
        st.plotly_chart(fig, width="stretch", config=PLOTLY_CONFIG)

        # seed / clamp the stored range against the current data
        _lo, _hi = st.session_state.get("flt_dates", (dmin, dmax))
        st.session_state["flt_dates"] = (
            min(max(_lo, dmin), dmax), min(max(_hi, dmin), dmax))
        f_dates = st.slider(
            "Date range", min_value=dmin, max_value=dmax, key="flt_dates",
            format="YYYY-MM-DD", label_visibility="collapsed",
        )

    # ── APPLY FILTERS ────────────────────────────────────────────────────────
    mask = stories["n_crawled"].between(f_crawled[0], f_crawled[1])
    if f_images:
        mask &= stories["has_images"]
    if f_domains:
        if f_domains_all:
            mask &= stories["crawled_domains"].apply(lambda ds: all(d in ds for d in f_domains))
        else:
            mask &= stories["crawled_domains"].apply(lambda ds: any(d in f_domains for d in ds))
    mask &= stories["date"].apply(lambda d: pd.notna(d) and f_dates[0] <= d <= f_dates[1])
    filtered = stories[mask].sort_values("date", ascending=False)

    with col_main:
        # ── Header: metrics + sunburst ────────────────────────────────────────
        head_l, head_r = st.columns([2.4, 1])

        f_articles = articles[articles["slug"].isin(filtered["slug"])] if not articles.empty else articles
        with head_l:
            m1, m2, m3 = st.columns(3)
            m1.metric("Stories", f"{len(filtered):,} / {len(stories):,}")
            m2.metric("Crawled articles", f"{len(f_articles):,}")
            m3.metric("Fully crawled", f"{(filtered['n_crawled'] == 3).sum():,}")

        with head_r:
            if f_articles.empty:
                st.caption("No crawled articles in the current filter.")
            else:
                counts = f_articles.groupby(["stance_key", "domain"]).size()
                ids, labels, parents, values, colors = [], [], [], [], []
                for stance in STANCES:
                    if stance not in counts.index.get_level_values(0):
                        continue
                    sub = counts[stance].sort_values(ascending=False)
                    ids.append(stance)
                    labels.append(STANCE_LABEL[stance])
                    parents.append("")
                    values.append(int(sub.sum()))
                    colors.append(STANCE_COLOR[stance])
                    for k, (dom, val) in enumerate(sub.items()):
                        ids.append(f"{stance}/{dom}")
                        labels.append(dom.replace(".com", ""))
                        parents.append(stance)
                        values.append(int(val))
                        colors.append(tint(STANCE_COLOR[stance], 0.25 + 0.5 * k / max(len(sub) - 1, 1)))
                fig = go.Figure(go.Sunburst(
                    ids=ids, labels=labels, parents=parents, values=values,
                    branchvalues="total",
                    marker=dict(colors=colors, line=dict(color="#fcfcfb", width=2)),
                    insidetextorientation="radial",
                    hovertemplate="%{label}: %{value} articles (%{percentRoot:.0%})<extra></extra>",
                ))
                fig.update_layout(**PLOTLY_LAYOUT, height=230)
                fig.update_layout(margin=dict(l=0, r=0, t=0, b=0))
                st.plotly_chart(fig, width="stretch", config=PLOTLY_CONFIG)

        if filtered.empty:
            st.info("No stories match the current filters.")
            st.stop()

        # ── Story selection: one searchbox with live suggestions ─────────────
        match_summaries = st.checkbox(
            "Filter by summary as well",
            key="search_summaries",
            help="When enabled, the words you type are also searched in the "
                 "AllSides story summaries, not just the titles.",
        )

        def search_stories(q: str) -> list:
            words = q.strip().lower().split()
            if not words:
                return []
            hits = []
            for _, row in filtered.iterrows():
                hay = row["headline"].lower()
                if match_summaries:
                    hay += " " + row["summary"].lower()
                if all(w in hay for w in words):
                    hits.append((f"{row['date']} · {row['headline']}", row["slug"]))
                    if len(hits) >= 30:
                        break
            return hits

        s1, s2 = st.columns([4, 1])
        with s1:
            # key depends on the checkbox: the component caches results per
            # search term, so toggling must start it fresh
            picked = st_searchbox(
                search_stories,
                label="Select a story",
                placeholder="Type words from the story title …",
                key=f"story_searchbox_{match_summaries}",
                help="Suggestions match every word you type against the AllSides "
                     "story titles (and summaries, if enabled above).",
            )
        with s2:
            st.write("")  # vertical alignment
            if st.button("🎲 Random", width="stretch"):
                st.session_state["story_slug"] = random.choice(filtered["slug"].tolist())

        if picked:
            st.session_state["story_slug"] = picked
        slug = st.session_state.get("story_slug")
        if slug not in set(filtered["slug"]):
            slug = filtered.iloc[0]["slug"]  # newest story matching the filters
        story = filtered[filtered["slug"] == slug].iloc[0]

        # ── Story header ─────────────────────────────────────────────────────
        st.markdown(f"### {story['headline']}")
        meta_bits = [str(story["date"] or "—"), story["topic"] or "—"]
        if story["tags"]:
            meta_bits.append(" · ".join(story["tags"][:5]))
        st.caption("  |  ".join(meta_bits))
        if story["summary"]:
            st.markdown(story["summary"].replace("$", "\\$"))
        st.markdown(f"[↗ View on AllSides]({story['allsides_link']})")

        # ── The 3 stance cards ────────────────────────────────────────────────
        card_cols = st.columns(3, gap="small")
        for stance, col in zip(STANCES, card_cols):
            meta = story[f"stance_{stance}"]
            art = crawled.get((story["slug"], stance))
            is_crawled = art is not None
            with col:
                if not isinstance(meta, dict):
                    st.markdown(
                        f'<div class="stance-card missing" style="--stance-color:{STANCE_COLOR[stance]}">'
                        f'<div class="card-top"><span class="stance-name">{STANCE_LABEL[stance]}</span>'
                        f'<span class="crawl-flag nok">✗</span></div>'
                        f'<div class="ex">No {STANCE_LABEL[stance]} article for this story.</div></div>',
                        unsafe_allow_html=True,
                    )
                    continue

                src = html.escape(meta.get("source", "—"))
                hl = html.escape(meta.get("headline", "") or "(no headline)")
                excerpt = html.escape((meta.get("summary", "") or "")[:220])
                if len(meta.get("summary", "") or "") > 220:
                    excerpt += "…"
                flag = '<span class="crawl-flag ok" title="Crawled">✓</span>' if is_crawled \
                    else '<span class="crawl-flag nok" title="Not crawled">✗</span>'
                extra_cls = "" if is_crawled else " missing"

                # AllSides stance image, stored locally by allsides_crawler.py
                img_html = ""
                local_img = meta.get("image_local_path")
                if local_img:
                    abs_img = os.path.join(os.path.dirname(stories_path), local_img)
                    if os.path.isfile(abs_img):
                        uri = thumb_data_uri(abs_img)
                        if uri:
                            img_html = f'<img class="card-img" src="{uri}" alt="">'

                st.markdown(
                    f'<div class="stance-card{extra_cls}" style="--stance-color:{STANCE_COLOR[stance]}">'
                    f'<div class="card-top"><span class="stance-name">{STANCE_LABEL[stance]}</span>'
                    f'{flag}</div>'
                    f'{img_html}'
                    f'<div class="src">{bias_chip(meta.get("rating", ""))} {src}</div>'
                    f'<div class="hl">{hl}</div>'
                    f'<div class="ex">{excerpt}</div></div>',
                    unsafe_allow_html=True,
                )
                if is_crawled:
                    if st.button("📄 View crawled article", key=f"view_{story['slug']}_{stance}",
                                 width="stretch"):
                        st.session_state["view_article"] = (story["slug"], stance)
                        st.rerun()
                else:
                    ext_url = meta.get("link") or story["allsides_link"]
                    st.link_button("↗ Go to external website", ext_url, width="stretch")
