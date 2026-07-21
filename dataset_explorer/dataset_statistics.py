import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from common import BIAS_COLOR, GRIDLINE, PLOTLY_CONFIG, PLOTLY_LAYOUT, SEQ_BLUE, STANCES, bias_chip

stories = st.session_state["stories"]
articles = st.session_state["articles"]
domain_bias = st.session_state["domain_bias"]

n_stories = len(stories)
n_articles = len(articles)
n_domains = articles["domain"].nunique() if not articles.empty else 0
_dates = stories["date"].dropna()
dmin, dmax = _dates.min(), _dates.max()

st.markdown(f"""
## The MUWS Dataset

The **MUWS** dataset pairs [AllSides](https://www.allsides.com) *balanced news* stories with locally
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
