"""
Dataset Explorer — Streamlit App
=======================================
Launch:  streamlit run dataset_explorer.py
"""

import hashlib
import json
import os
import random

import pandas as pd
import streamlit as st

# ── CONFIGURATION ────────────────────────────────────────────────────────────

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_DATA_DIR = os.path.normpath(os.path.join(_SCRIPT_DIR, "output", "full_articles"))

RATING_ORDER = ["left", "lean left", "center", "lean right", "right"]

# ── HELPERS ──────────────────────────────────────────────────────────────────

def load_from_uploaded(uploaded_files: list) -> pd.DataFrame:
    rows: list[dict] = []
    for uf in uploaded_files:
        data = json.load(uf)
        for topic_slug, stances in data.items():
            for stance_key, article in stances.items():
                rows.append({
                    "source_file": uf.name,
                    "topic_slug": topic_slug,
                    "stance_key": stance_key,
                    **article,
                })
    return _post_process(pd.DataFrame(rows)) if rows else pd.DataFrame()


@st.cache_resource(show_spinner="Loading dataset from directory …")
def load_from_directory(data_dir: str) -> pd.DataFrame:
    rows: list[dict] = []
    for fname in sorted(os.listdir(data_dir)):
        if not fname.endswith(".json"):
            continue
        with open(os.path.join(data_dir, fname)) as f:
            data = json.load(f)
        for topic_slug, stances in data.items():
            for stance_key, article in stances.items():
                rows.append({
                    "source_file": fname,
                    "topic_slug": topic_slug,
                    "stance_key": stance_key,
                    **article,
                })
    return _post_process(pd.DataFrame(rows)) if rows else pd.DataFrame()


def _post_process(df: pd.DataFrame) -> pd.DataFrame:
    if "extracted_body_text" in df.columns:
        df["text_length"] = df["extracted_body_text"].fillna("").str.len()
    df["uid"] = df.index.astype(str)
    return df


def safe_list(val) -> list:
    if isinstance(val, list):
        return val
    try:
        if pd.notna(val):
            return list(val) if hasattr(val, '__iter__') and not isinstance(val, str) else []
    except (TypeError, ValueError):
        pass
    return []


# ── PAGE CONFIG ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="MUWS AllSides Dataset Explorer",
    page_icon="📰",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
[data-testid="stMetric"] {
    background: #f0f2f6; border: 1px solid #d1d5db;
    border-radius: 8px; padding: 12px 16px;
}
[data-testid="stMetric"] label {
    color: #555 !important;
}
[data-testid="stMetric"] [data-testid="stMetricValue"] {
    color: #1a1a2e !important;
}
[data-testid="stMetric"] [data-testid="stMetricDelta"] {
    color: inherit !important;
}
.block-container { padding-top: 1.2rem; }
div[data-testid="stExpander"] details {
    border: 1px solid #e1e4e8; border-radius: 8px;
}
</style>
""", unsafe_allow_html=True)

# ── SIDEBAR — DATA LOADING ──────────────────────────────────────────────────
with st.sidebar:
    st.title("📰 MUWS Dataset Explorer")

    load_method = st.radio("Load dataset", ["Local directory path", "Upload JSON files"], index=0)

    data_dir = None
    if load_method == "Local directory path":
        data_dir = st.text_input("Directory path", value=_DEFAULT_DATA_DIR)
    else:
        uploaded = st.file_uploader(
            "Drop per-domain JSON files here",
            type=["json"],
            accept_multiple_files=True,
        )

    st.divider()

    st.subheader("Filters")

# ── LOAD DATA ────────────────────────────────────────────────────────────────
df = pd.DataFrame()

if load_method == "Local directory path":
    if not os.path.isdir(data_dir):
        st.error(f"Directory not found: `{data_dir}`")
        st.stop()
    df = load_from_directory(data_dir)
else:
    if uploaded:
        cache_key = hashlib.md5("".join(f.name for f in uploaded).encode()).hexdigest()
        if st.session_state.get("_upload_key") != cache_key:
            st.session_state["_upload_key"] = cache_key
            st.session_state["_upload_df"] = load_from_uploaded(uploaded)
        df = st.session_state.get("_upload_df", pd.DataFrame())
    else:
        st.info("Upload one or more per-domain JSON files to get started.")
        st.stop()

if df.empty:
    st.warning("No articles found. Check your directory.")
    st.stop()

# ── SIDEBAR FILTERS ──────────────────────────────────────────────────────────
with st.sidebar:
    filter_domain = st.multiselect("Source / Domain", sorted(df["domain"].unique()), default=[])
    filter_rating = st.multiselect("Rating", RATING_ORDER, default=[])
    filter_stance = st.multiselect("Stance Key", sorted(df["stance_key"].unique()), default=[])

filtered = df.copy()
if filter_domain:
    filtered = filtered[filtered["domain"].isin(filter_domain)]
if filter_rating:
    filtered = filtered[filtered["rating"].isin(filter_rating)]
if filter_stance:
    filtered = filtered[filtered["stance_key"].isin(filter_stance)]

if filtered.empty:
    st.info("No articles match the current filters.")
    st.stop()

# ══════════════════════════════════════════════════════════════════════════════
#  ARTICLE INSPECTOR
# ══════════════════════════════════════════════════════════════════════════════
st.header("Article Inspector")
st.caption(f"**{len(filtered):,}** articles match your filters.")

# ── Article selection ────────────────────────────────────────────────────────
sel1, sel2 = st.columns([4, 1], vertical_alignment="bottom")
with sel1:
    options_map = {}
    for idx, row in filtered.iterrows():
        hl = row["extracted_headline"][:70] if pd.notna(row.get("extracted_headline")) else row["topic_slug"][:50]
        label = f"[{row['domain']}] {hl}  ({row['stance_key']})"
        options_map[label] = idx
    selected_label = st.selectbox("Select an article", list(options_map.keys()), index=0)
with sel2:
    if st.button("🎲 Random", width="stretch"):
        st.session_state["_rand_idx"] = random.choice(filtered.index.tolist())
        st.rerun()

if "_rand_idx" in st.session_state:
    rand_idx = st.session_state.pop("_rand_idx")
    art = filtered.loc[rand_idx] if rand_idx in filtered.index else filtered.iloc[0]
else:
    art = filtered.loc[options_map[selected_label]]

st.divider()

# ── Metadata cards ───────────────────────────────────────────────────────────
m1, m2, m3, m4 = st.columns(4)
m1.metric("Domain", art["domain"])
m2.metric("Rating", art.get("rating", "—"))
m3.metric("Stance", art.get("stance_key", "—"))
m4.metric("HTTP", art.get("http_status_code", "—"))

m6, m7 = st.columns(2)
m6.metric("Text Length", f"{art.get('text_length', 0):,} chars" if pd.notna(art.get("text_length")) else "—")
slug = str(art["topic_slug"])
m7.metric("Topic", slug[:30] + "…" if len(slug) > 30 else slug)

url = art.get("url", "")
if url:
    st.markdown(f'🔗 **URL:** `{url}`')
st.caption(f"Source file: `{art.get('source_file', '—')}`")

st.divider()

st.subheader("Extracted Content")

# ── Article headline + raw JSON toggle ─────────────────────────────────
headline = art.get("extracted_headline")
if headline and str(headline).strip():
    st.markdown(f"# {str(headline).strip()}")

json_fields = [
    "domain", "url", "rating", "http_status_code",
    "execution_status", "extracted_headline", "extracted_body_text",
    "error_payload", "extracted_images", "extracted_interactives",
    "extracted_videos",
]
raw_obj = {}
for field in json_fields:
    val = art.get(field)
    if val is not None:
        try:
            if pd.notna(val):
                raw_obj[field] = val
        except (TypeError, ValueError):
            raw_obj[field] = val

with st.expander("🔍 Raw JSON for this datapoint"):
    st.code(json.dumps(raw_obj, indent=2, default=str), language="json")

body = str(art.get("extracted_body_text", "") or "")

# ── Full article body (rendered as markdown) ─────────────────────────────
with st.expander("📝 Full Article Text", expanded=True):
    if body.strip():
        st.markdown(body.replace("$", "\\$"))
    else:
        st.warning("No body text extracted.")

# ── Images with captions ─────────────────────────────────────────────────
images = safe_list(art.get("extracted_images"))
if images:
    with st.expander(f"🖼️ Images ({len(images)})", expanded=True):
        for i, img in enumerate(images):
            if isinstance(img, dict):
                local_path = img.get("local_path", "")
                alt = img.get("alt", "")
                caption = img.get("caption", "")
            else:
                local_path, alt, caption = "", "", ""

            if not local_path or not data_dir:
                continue
            abs_path = os.path.join(os.path.dirname(data_dir), local_path)
            if not os.path.isfile(abs_path):
                continue

            st.image(abs_path, width="stretch")
            info_parts = []
            if caption:
                info_parts.append(f"**Caption:** {caption}")
            if alt:
                info_parts.append(f"**Alt:** {alt}")
            info_parts.append(f"📁 `{local_path}`")
            st.markdown("  \n".join(info_parts))
            if i < len(images) - 1:
                st.divider()
else:
    st.info("No images extracted.")

# ── Videos ───────────────────────────────────────────────────────────────
videos = safe_list(art.get("extracted_videos"))
if videos:
    with st.expander(f"🎬 Videos ({len(videos)})"):
        for v in videos[:5]:
            st.code(v.get("url", str(v)) if isinstance(v, dict) else str(v), language=None)

# ── Error payload ────────────────────────────────────────────────────────
err = art.get("error_payload")
if err and str(err).strip() and str(err) != "None":
    with st.expander("⚠️ Error Payload"):
        st.code(str(err), language="json")
