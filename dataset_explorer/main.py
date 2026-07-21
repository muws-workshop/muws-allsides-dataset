"""
MUWS Dataset Explorer — Streamlit App
=======================================
Story-centric explorer for the AllSides balanced-news dataset + local crawl.

Launch:  streamlit run dataset_explorer.py
"""

import os
import streamlit as st
from common import STANCE_COLOR, build_story_index, load_crawled, tint

# CONFIGURATION
_OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "output")
_DEFAULT_DATA_DIR = os.path.normpath(os.path.join(_OUTPUT_DIR, "full_articles"))
_DEFAULT_STORIES_PATH = os.path.join(_OUTPUT_DIR, "allsides_Jan2025_May2026_combined.jsonl")


# PAGE CONFIG & CSS
st.set_page_config(
    page_title="MUWS Dataset Explorer",
    page_icon="📰",
    layout="wide",
    initial_sidebar_state="expanded",
)

# "missing" (not-crawled) stance card
_is_dark_theme = st.context.theme.type == "dark"
_missing_card_style = (
    "opacity: 0.55; background: #f0f0ee;" if _is_dark_theme
    else "opacity: 0.85; background: #e2e2df;"
)
# Stance-card action buttons
_stance_button_css = "\n".join(
    f'div[class*="st-key-view_{s}_"] button {{'
    f' background: transparent !important; border: 2px solid {STANCE_COLOR[s]} !important;'
    f' color: {STANCE_COLOR[s]} !important; }}\n'
    f'div[class*="st-key-view_{s}_"] button:hover {{ background: {tint(STANCE_COLOR[s], 0.85)} !important; }}\n'
    f'div[class*="st-key-view_{s}_active_"] button {{'
    f' background: {STANCE_COLOR[s]} !important; color: #fff !important; }}\n'
    f'div[class*="st-key-view_{s}_active_"] button:hover {{ opacity: 0.85 !important; }}\n'
    f'div[class*="st-key-extlink_{s}_"] a {{'
    f' background: transparent !important; border: 2px dashed {STANCE_COLOR[s]} !important;'
    f' color: {STANCE_COLOR[s]} !important; }}\n'
    f'div[class*="st-key-extlink_{s}_"] a:hover {{ background: {tint(STANCE_COLOR[s], 0.85)} !important; }}'
    for s in ("left", "center", "right")
)

st.markdown(f"""
<style>
[data-testid="stMetric"] {{
    background: #f0f2f6; border: 1px solid #d1d5db;
    border-radius: 8px; padding: 10px 14px;
}}
[data-testid="stMetric"] label {{ color: #555 !important; }}
[data-testid="stMetric"] [data-testid="stMetricValue"] {{ color: #1a1a2e !important; font-size: 1.5rem; }}

div[data-testid="stExpander"] details {{ border: 1px solid #e1e4e8; border-radius: 8px; }}

{_stance_button_css}

.stance-card {{
    border: 1px solid #d1d5db; border-radius: 10px;
    padding: 14px 16px; min-height: 160px; margin-bottom: 16px;
    background: #fcfcfb; border-top: 4px solid var(--stance-color, #898781);
}}
.stance-card.missing {{ {_missing_card_style} }}
.stance-card .card-top {{
    display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;
}}
.stance-card .stance-name {{ font-weight: 700; color: var(--stance-color); font-size: 0.95rem; }}
.stance-card .crawl-flag {{ font-size: 1rem; }}
.stance-card .card-img {{
    width: 100%; height: 120px; object-fit: cover;
    border-radius: 6px; margin-bottom: 8px; display: block;
}}
.stance-card .src {{ color: #52514e; font-size: 0.82rem; margin-bottom: 6px; }}
.stance-card .hl {{ font-weight: 600; color: #0b0b0b; font-size: 0.95rem; line-height: 1.3; margin-bottom: 6px; }}
.stance-card .ex {{ color: #52514e; font-size: 0.82rem; line-height: 1.45; }}
.bias-chip {{
    display: inline-block; min-width: 20px; text-align: center;
    border-radius: 4px; padding: 0 4px; margin-right: 4px;
    font-size: 0.7rem; font-weight: 700;
}}
.crawl-flag.ok {{ color: #0ca30c; font-weight: 800; }}
.crawl-flag.nok {{ color: #d03b3b; font-weight: 800; }}
</style>
""", unsafe_allow_html=True)


# Pages
dataset_statistics = st.Page("dataset_statistics.py", title="Dataset Statistics", icon="📊")
story_viewer = st.Page("story_viewer.py", title="Story Viewer", icon="📖")

pg = st.navigation([dataset_statistics, story_viewer], position="hidden")

# SIDEBAR: HEADER, NAVIGATION, DATA SOURCES
with st.sidebar:
    st.title("📰 MUWS Dataset Explorer")

    st.caption("Data Views")
    st.page_link(dataset_statistics)
    st.page_link(story_viewer)

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

# Share data with the navigation pages
st.session_state["stories_path"] = stories_path
st.session_state["data_dir"] = data_dir
st.session_state["stories"] = stories
st.session_state["crawled"] = crawled
st.session_state["articles"] = articles
st.session_state["domain_bias"] = domain_bias
st.session_state["bias_emoji"] = BIAS_EMOJI

# Persist filter state across page switches
_FILTER_DEFAULTS = {
    "flt_crawled": (0, 3),
    "flt_images": False,
    "flt_stances": [],
    "flt_domains": [],
    "flt_domains_all": False,
    "search_summaries": False,
}
for _k, _v in _FILTER_DEFAULTS.items():
    st.session_state[_k] = st.session_state.get(_k, _v)

pg.run()
