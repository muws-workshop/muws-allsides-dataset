import html
import json
import os
import random

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components
from streamlit_searchbox import st_searchbox

from common import (
    PLOTLY_CONFIG,
    PLOTLY_LAYOUT,
    SEQ_BLUE,
    STANCE_COLOR,
    STANCE_LABEL,
    STANCES,
    bias_chip,
    thumb_data_uri,
    tint,
)

stories = st.session_state["stories"]
crawled = st.session_state["crawled"]
articles = st.session_state["articles"]
domain_bias = st.session_state["domain_bias"]
BIAS_EMOJI = st.session_state["bias_emoji"]
stories_path = st.session_state["stories_path"]
data_dir = st.session_state["data_dir"]

# ══════════════════════════════════════════════════════════════════════════════
#  STORY EXPLORER
# ══════════════════════════════════════════════════════════════════════════════
left_col, right_col = st.columns([1.3, 1], gap="large")

with left_col:
    # ── FILTERS ────────────────────────────────────────────────────────────
    st.subheader("🔎 Filters")
    f_images = st.checkbox(
            "Only stories with crawled images 🖼️", key="flt_images",
            help="At least one crawled article has locally stored images (for multimodal work).",
        )
    filt_a, filt_b = st.columns(2, gap="medium")

    with filt_a:
        # f_crawled = st.slider(
        #     "Crawled stances (min / max)", 0, 3, key="flt_crawled",
        #     help="How many of the story's Left/Center/Right articles were successfully crawled.",
        # )
        f_stances = st.multiselect(
            "Must include full article these stances",
            options=STANCES,
            key="flt_stances",
            format_func=lambda s: STANCE_LABEL[s],
            help="Only show stories where full articles of every stance selected here was successfully "
                 "crawled — e.g. pick Left and Right to require both.",
        )
        all_domains = sorted(articles["domain"].unique()) if not articles.empty else []
        st.session_state["flt_domains"] = [
            d for d in st.session_state["flt_domains"] if d in all_domains
        ]
        f_domains = st.multiselect(
            "Must include full articles of news provider (up to 3)",
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

    with filt_b:
        # Date range: mini timeline + slider
        st.markdown("**Date Range**")
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
        fig.update_layout(**PLOTLY_LAYOUT, height=70, bargap=0.3)
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
    mask = pd.Series(True, index=stories.index)
    # mask &= stories["n_crawled"].between(f_crawled[0], f_crawled[1])
    if f_images:
        mask &= stories["has_images"]
    if f_stances:
        mask &= stories["crawled_stances"].apply(lambda ss: all(s in ss for s in f_stances))
    if f_domains:
        if f_domains_all:
            mask &= stories["crawled_domains"].apply(lambda ds: all(d in ds for d in f_domains))
        else:
            mask &= stories["crawled_domains"].apply(lambda ds: any(d in f_domains for d in ds))
    mask &= stories["date"].apply(lambda d: pd.notna(d) and f_dates[0] <= d <= f_dates[1])
    filtered = stories[mask].sort_values("date", ascending=False)
    f_articles = articles[articles["slug"].isin(filtered["slug"])] if not articles.empty else articles

    st.divider()

    # ── How much of the dataset the current filter covers ───────────────────
    st.markdown("**Data Coverage**")
    n_stories_total, n_articles_total = len(stories), len(articles)
    st.progress(
        len(filtered) / n_stories_total if n_stories_total else 0,
        text=f"Stories matching filter — {len(filtered):,} / {n_stories_total:,} "
             f"({len(filtered) / n_stories_total:.0%})" if n_stories_total else "Stories matching filter",
    )
    st.progress(
        len(f_articles) / n_articles_total if n_articles_total else 0,
        text=f"Crawled articles in those stories — {len(f_articles):,} / {n_articles_total:,} "
             f"({len(f_articles) / n_articles_total:.0%})" if n_articles_total else "Crawled articles in those stories",
    )

with right_col:
    st.subheader("Stance / Publisher Distribution")
    st.caption("Crawled articles in the current filter")
    if f_articles.empty:
        st.caption("No crawled articles in the current filter.")
    else:
        counts = f_articles.groupby(["stance_key", "domain"]).size()
        ids, labels, parents, sb_values, colors = [], [], [], [], []
        for stance in STANCES:
            if stance not in counts.index.get_level_values(0):
                continue
            sub = counts[stance].sort_values(ascending=False)
            ids.append(stance)
            labels.append(STANCE_LABEL[stance])
            parents.append("")
            sb_values.append(int(sub.sum()))
            colors.append(STANCE_COLOR[stance])
            for k, (dom, val) in enumerate(sub.items()):
                ids.append(f"{stance}/{dom}")
                labels.append(dom.replace(".com", ""))
                parents.append(stance)
                sb_values.append(int(val))
                colors.append(tint(STANCE_COLOR[stance], 0.25 + 0.5 * k / max(len(sub) - 1, 1)))
        fig = go.Figure(go.Sunburst(
            ids=ids, labels=labels, parents=parents, values=sb_values,
            branchvalues="total",
            marker=dict(colors=colors, line=dict(color="#fcfcfb", width=2)),
            insidetextorientation="radial",
            textfont=dict(size=14, color="#1a1a2e"),
            hovertemplate="%{label}: %{value} articles (%{percentRoot:.0%})<extra></extra>",
        ))
        fig.update_layout(**PLOTLY_LAYOUT, height=430)
        fig.update_layout(margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig, width="stretch", config=PLOTLY_CONFIG)

if filtered.empty:
    st.info("No stories match the current filters.")
    st.stop()

st.divider()

# ── Story selection: searchbox + inline dice button ───────────────────────
st.markdown("**Story Selection**")
match_summaries = st.checkbox(
    "Include summaries in search?",
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


# Resolve the current slug *before* rendering the searchbox, so its text
# can default to the currently-selected story's headline.
slug = st.session_state.get("story_slug")
if slug not in set(filtered["slug"]):
    slug = filtered.iloc[0]["slug"]  # newest story matching the filters
    st.session_state["story_slug"] = slug
current_headline = filtered.loc[filtered["slug"] == slug, "headline"].iloc[0]

s1, s2 = st.columns([11, 1], gap="small")
with s1:
    # key includes the slug: whenever it changes programmatically (dice,
    # or a filter change bumping the selection), the component remounts
    # and picks up the new default_searchterm instead of showing stale text.
    picked = st_searchbox(
        search_stories,
        label=None,
        placeholder="Type words from the story title …",
        default_searchterm=current_headline,
        key=f"story_searchbox_{match_summaries}_{slug}",
    )
with s2:
    if st.button("🎲", help="Jump to a random story"):
        st.session_state["story_slug"] = random.choice(filtered["slug"].tolist())
        st.rerun()

if picked:
    st.session_state["story_slug"] = picked
    slug = picked
story = filtered[filtered["slug"] == slug].iloc[0]

# ── Story header ─────────────────────────────────────────────────────────
st.markdown(f"### {story['headline']}")
meta_bits = [str(story["date"] or "—"), story["topic"] or "—"]
if story["tags"]:
    meta_bits.append(" · ".join(story["tags"][:5]))
st.caption("  |  ".join(meta_bits))
if story["summary"]:
    st.markdown(story["summary"].replace("$", "&#36;"))
st.markdown(f"[↗ View on AllSides]({story['allsides_link']})")

# ── The 3 stance cards ───────────────────────────────────────────────────
card_cols = st.columns(3, gap="small")
for stance, col in zip(STANCES, card_cols):
    meta = story[f"stance_{stance}"]
    art = crawled.get((story["slug"], stance))
    is_crawled = art is not None
    with col:
        # Button first, so all 3 line up regardless of card content length below.
        # Key is stance-prefixed (not slug-prefixed) with an "_active_" infix
        # when this article is open, so main.py's CSS can color-code and fill
        # it by stance via a stable key substring.
        if isinstance(meta, dict) and is_crawled:
            is_open = st.session_state.get("view_article") == (story["slug"], stance)
            view_key = f"view_{stance}_{'active_' if is_open else ''}{story['slug']}"
            if st.button("📄 View crawled article", key=view_key, width="stretch"):
                st.session_state["view_article"] = (story["slug"], stance)
                st.rerun()
        elif isinstance(meta, dict):
            ext_url = meta.get("link") or story["allsides_link"]
            st.link_button("↗ Go to external website", ext_url, width="stretch",
                            key=f"extlink_{stance}_{story['slug']}")
        else:
            st.button("— not available —", disabled=True, width="stretch",
                      key=f"nolink_{story['slug']}_{stance}")

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
        excerpt = html.escape(meta.get("summary", "") or "")
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

# ══════════════════════════════════════════════════════════════════════════════
#  INLINE ARTICLE VIEW — shown below the stance cards when one is selected
# ══════════════════════════════════════════════════════════════════════════════
view_article = st.session_state.get("view_article")
if view_article and view_article[0] == story["slug"]:
    v_slug, v_stance = view_article
    art = crawled.get((v_slug, v_stance))

    st.markdown('<div id="article-anchor"></div>', unsafe_allow_html=True)
    # Only scroll on the render where the article just changed — otherwise
    # every later widget interaction (e.g. the image carousel's prev/next)
    # would keep snapping the page back down to it.
    if st.session_state.get("_scrolled_to_article") != view_article:
        components.html("""
            <script>
                const anchor = window.parent.document.getElementById('article-anchor');
                if (anchor) { anchor.scrollIntoView({behavior: 'smooth', block: 'start'}); }
            </script>
        """, height=0)
        st.session_state["_scrolled_to_article"] = view_article

    st.divider()

    if st.button("✕ Close article", key="close_article"):
        del st.session_state["view_article"]
        st.rerun()

    if art is None:
        st.warning("Article not found — it may not be crawled.")
    else:
        body = str(art.get("extracted_body_text", "") or "")

        valid_images = []
        for img in art.get("extracted_images") or []:
            if not isinstance(img, dict):
                continue
            local_path = img.get("local_path", "")
            if not local_path:
                continue
            abs_path = os.path.join(os.path.dirname(data_dir), local_path)
            if os.path.isfile(abs_path):
                valid_images.append((img, abs_path))

        headline = art.get("extracted_headline")
        if headline and str(headline).strip():
            st.markdown(f"## {str(headline).strip()}")

        if art.get("url"):
            st.markdown(f'🔗 **URL:** `{art["url"]}`')
        st.caption(f"Source file: `{art.get('source_file', '—')}`")

        info_col, image_col = st.columns([1.3, 1], gap="large")

        with info_col:
            stat_row1 = st.columns(2)
            stat_row1[0].metric("Domain", art.get("domain", "—"))
            stat_row1[1].metric("Rating", art.get("rating", "—"))
            stat_row2 = st.columns(2)
            stat_row2[0].metric("Status", art.get("execution_status", "—"))
            stat_row2[1].metric("Text length", f"{len(body):,} chars")

        with image_col:
            if valid_images:
                idx_key = f"img_idx_{v_slug}_{v_stance}"
                n_images = len(valid_images)
                st.session_state[idx_key] = st.session_state.get(idx_key, 0) % n_images

                nav_l, nav_c, nav_r = st.columns([1, 5, 1], vertical_alignment="center")
                with nav_l:
                    if st.button("◀", key=f"img_prev_{idx_key}", width="stretch", disabled=n_images <= 1):
                        st.session_state[idx_key] = (st.session_state[idx_key] - 1) % n_images
                        st.rerun()
                with nav_r:
                    if st.button("▶", key=f"img_next_{idx_key}", width="stretch", disabled=n_images <= 1):
                        st.session_state[idx_key] = (st.session_state[idx_key] + 1) % n_images
                        st.rerun()

                cur_img, cur_path = valid_images[st.session_state[idx_key]]
                with nav_c:
                    st.image(cur_path, width="stretch")

                caption_bits = [f"🖼️ Image {st.session_state[idx_key] + 1} / {n_images}"]
                if cur_img.get("caption"):
                    caption_bits.append(cur_img["caption"])
                st.caption("  —  ".join(caption_bits))

        st.divider()

        # Full body directly on the page — no expander
        if body.strip():
            st.markdown(body.replace("$", "&#36;"))
        else:
            st.warning("No body text extracted.")

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
