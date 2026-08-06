import os
from datetime import datetime, timezone

import pandas as pd
import plotly.graph_objects as go
import pycountry
import streamlit as st
from streamlit_plotly_events import plotly_events

from app.collectors import CATEGORIES, collect_all, collect_country
from app.database import country_articles, country_cache_fresh, database_stats, init_db, provider_status, recent_signals

st.set_page_config(page_title="Horizon Early Warning", page_icon="◉", layout="wide", initial_sidebar_state="expanded")
init_db()

SOURCE_COLORS = {"GDELT":"#2563eb","GDACS":"#ef6548","USGS":"#8854d0","NEWS":"#09a88b"}
SEVERITY_COLORS = {"Critical":"#dc3e45","High":"#ed8a34","Watch":"#d5a727"}

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Manrope:wght@600;700;800&display=swap');
:root{--blue:#2567df;--ink:#182438;--muted:#718096;--line:#e1e7ee;--bg:#f4f7fb}
.stApp{background:var(--bg);color:var(--ink);font-family:'DM Sans',sans-serif}.main .block-container{max-width:1600px;padding:1.1rem 1.5rem 2rem}
[data-testid="stSidebar"]{background:#fff;border-right:1px solid var(--line)}[data-testid="stSidebar"] .block-container{padding-top:1.2rem}
h1,h2,h3{font-family:'Manrope',sans-serif!important;letter-spacing:-.02em}.brand{display:flex;align-items:center;gap:.65rem;margin:.1rem 0 1.5rem}.brandmark{width:38px;height:38px;border-radius:10px;background:linear-gradient(135deg,#2874eb,#122965);display:grid;place-items:center;color:white;font-weight:800}.brand strong{display:block;font:800 17px Manrope}.brand span{font-size:8px;color:#2b6ee8;font-weight:800;letter-spacing:1px}
.eyebrow{font-size:9px;letter-spacing:1.2px;font-weight:800;color:#64748b;margin-bottom:.15rem}.subtitle{color:#738095;font-size:13px;margin-top:-.4rem}.metric{background:#fff;border:1px solid var(--line);border-radius:10px;padding:14px 16px;box-shadow:0 2px 10px #243b5a10;min-height:96px}.metric span{font-size:9px;font-weight:800;color:#77859a;letter-spacing:.7px}.metric b{display:block;font:800 28px Manrope;margin:5px 0 1px}.metric small{color:#7d8999;font-size:9px}.panel{background:#fff;border:1px solid var(--line);border-radius:11px;padding:15px 17px;box-shadow:0 2px 12px #263b5510}.health{display:inline-flex;align-items:center;gap:5px;border:1px solid #dfe5ec;border-radius:12px;padding:4px 8px;margin-right:5px;background:#fff;font-size:9px}.health i{width:6px;height:6px;border-radius:50%;background:#20aa78}.health.unavailable i{background:#dc4b53}.event{border-bottom:1px solid #edf0f4;padding:10px 2px}.event b{font-size:12px}.event p{font-size:9px;color:#7d8999;margin:3px 0}.pill{display:inline-block;border-radius:12px;padding:3px 7px;font-size:8px;font-weight:800}.country-head{padding:10px 0;border-bottom:1px solid var(--line);margin-bottom:12px}.country-head h2{margin:0}.brief{background:#f0f5ff;border:1px solid #bfd4f7;border-left:3px solid #2869df;border-radius:8px;padding:13px 14px;margin-bottom:12px}.brief b{color:#245dbd}.brief p{font-size:11px;line-height:1.55;color:#4c607a}.article{border:1px solid #dfe5ec;background:#fcfdff;border-radius:9px;padding:12px 14px;margin:8px 0}.article .time{font-size:9px;color:#526b8f;font-weight:700}.article h4{font:800 13px Manrope;margin:7px 0}.article .sum{background:#f4f8ff;border-left:2px solid #5688df;padding:8px 10px;border-radius:5px;font-size:10px;color:#4b5d75}.article footer{font-size:9px;color:#64748b;margin-top:8px}.article a{float:right;color:#2868da;text-decoration:none;font-weight:700}
div.stButton>button{border-radius:7px;font-weight:700;border:1px solid #dce3eb}.stTabs [data-baseweb="tab-list"]{gap:8px}.stTabs [data-baseweb="tab"]{background:#f4f6f9;border-radius:7px;padding:8px 13px}.stTabs [aria-selected="true"]{background:#eaf2ff!important;color:#2667d7!important}
</style>
""", unsafe_allow_html=True)

with st.sidebar:
    st.markdown('<div class="brand"><div class="brandmark">HO</div><div><strong>Horizon</strong><span>EARLY WARNING</span></div></div>', unsafe_allow_html=True)
    nav = st.radio("Navigation", ["Overview", "Signal map", "Country intelligence", "Alerts", "Sources", "Database"], label_visibility="collapsed")
    st.divider()
    st.caption("Docker + Streamlit")
    st.caption("Persistent SQLite storage")

if "collected" not in st.session_state:
    st.session_state.collected = False
if "country" not in st.session_state:
    st.session_state.country = "United States"
if "pending_country_refresh" not in st.session_state:
    st.session_state.pending_country_refresh = None

COUNTRY_NAME_OVERRIDES = {
    "Bolivia, Plurinational State of": "Bolivia", "Brunei Darussalam": "Brunei",
    "Congo, The Democratic Republic of the": "Democratic Republic of the Congo",
    "Iran, Islamic Republic of": "Iran", "Korea, Democratic People's Republic of": "North Korea",
    "Korea, Republic of": "South Korea", "Lao People's Democratic Republic": "Laos",
    "Moldova, Republic of": "Moldova", "Russian Federation": "Russia",
    "Syrian Arab Republic": "Syria", "Taiwan, Province of China": "Taiwan",
    "Tanzania, United Republic of": "Tanzania", "Venezuela, Bolivarian Republic of": "Venezuela",
    "Viet Nam": "Vietnam", "Palestine, State of": "Palestine",
}
COUNTRIES = sorted(
    [(item.alpha_3, COUNTRY_NAME_OVERRIDES.get(item.name, item.name)) for item in pycountry.countries],
    key=lambda item: item[1],
)
COUNTRY_NAMES = [name for _, name in COUNTRIES]

if st.session_state.pending_country_refresh:
    pending_country = st.session_state.pending_country_refresh
    st.session_state.pending_country_refresh = None
    if not country_cache_fresh(pending_country):
        with st.spinner(f"Collecting the five latest reports in every category for {pending_country}…"):
            collect_country(pending_country)

top1, top2 = st.columns([5, 1])
with top1:
    st.markdown('<div class="eyebrow">GLOBAL OPERATIONS / LIVE VIEW</div>', unsafe_allow_html=True)
    st.title("Intelligence overview")
    st.markdown('<p class="subtitle">Monitor emerging risk, event concentration and verified open-source signals.</p>', unsafe_allow_html=True)
with top2:
    refresh = st.button("↻ Live refresh", type="primary", use_container_width=True)

if refresh or not st.session_state.collected:
    with st.spinner("Collecting GDELT, GDACS and USGS signals…"):
        collect_all()
    st.session_state.collected = True

signals = recent_signals(100); stats = database_stats(); health = provider_status()
m1,m2,m3,m4 = st.columns(4)
metrics = [
    (m1,"ACTIVE SIGNALS",len(signals),"current stored events"),
    (m2,"HIGH PRIORITY",sum(s["severity"] in ("Critical","High") for s in signals),"requiring review"),
    (m3,"GLOBAL COVERAGE",len({s["location"] for s in signals}),"monitored locations"),
    (m4,"STORED ARTICLES",stats["articles"],"persistent SQLite records"),
]
for col,label,value,note in metrics:
    col.markdown(f'<div class="metric"><span>{label}</span><b>{value}</b><small>{note}</small></div>', unsafe_allow_html=True)

st.write("")
status_html = '<div class="panel"><b style="font-size:10px;margin-right:10px">Provider health</b>'
for provider in ["GDELT","GDACS","USGS","Global news"]:
    state = health.get(provider, {}).get("status", "standby")
    status_html += f'<span class="health {state}"><i></i>{provider} · {state}</span>'
status_html += '</div>'
st.markdown(status_html, unsafe_allow_html=True)

left, right = st.columns([2.45, 1], gap="medium")
with left:
    st.markdown('<div class="panel"><h3 style="margin:0">Global signal map</h3><p class="subtitle">Hover to identify any country. Click a country to load its latest intelligence, or click a sensor for event intelligence.</p></div>', unsafe_allow_html=True)
    fig = go.Figure()
    fig.add_trace(go.Choropleth(
        locations=[code for code, _ in COUNTRIES], locationmode="ISO-3",
        z=[1] * len(COUNTRIES), customdata=[name for _, name in COUNTRIES],
        colorscale=[[0, "#dce8e8"], [1, "#dce8e8"]], showscale=False,
        marker_line_color="#8faab4", marker_line_width=0.55, name="Countries",
        hovertemplate="<b>%{customdata}</b><br>Click for country intelligence<extra></extra>",
    ))
    if signals:
        df = pd.DataFrame(signals).dropna(subset=["latitude","longitude"])
        for source, group in df.groupby("source"):
            fig.add_trace(go.Scattergeo(lon=group.longitude, lat=group.latitude, mode="markers",
                marker=dict(size=12, color=SOURCE_COLORS.get(source,"#09a88b"), line=dict(width=2,color="white")),
                text=group.title, customdata=group[["id","source"]].values, name=source,
                hovertemplate="<b>%{text}</b><br>%{customdata[1]}<extra></extra>"))
    fig.update_geos(showcountries=True, countrycolor="#91aab2", showcoastlines=True,
        coastlinecolor="#89a6b2", showland=True, landcolor="#dce8e8", showocean=True, oceancolor="#d8edf3",
        projection_type="natural earth")
    fig.update_layout(height=470, margin=dict(l=0,r=0,t=0,b=0), legend=dict(orientation="h",y=.02,x=.02),
        paper_bgcolor="white", geo_bgcolor="#d8edf3", clickmode="event+select")
    clicks = plotly_events(fig, click_event=True, hover_event=False, select_event=False, override_height=470, key="signal-map")
    if clicks:
        curve = clicks[0].get("curveNumber", 0); point = clicks[0].get("pointNumber", 0)
        if curve == 0:
            try:
                clicked_country = fig.data[0].customdata[point]
                if clicked_country != st.session_state.get("last_map_country"):
                    st.session_state.last_map_country = clicked_country
                    st.session_state.country = clicked_country
                    st.session_state.country_picker = clicked_country
                    st.session_state.pending_country_refresh = clicked_country
                    st.rerun()
            except Exception:
                pass
        else:
            try:
                source = fig.data[curve].name; selected_id = fig.data[curve].customdata[point][0]
                st.session_state.selected_signal = selected_id
            except Exception:
                pass

    st.markdown('<div class="panel"><h3 style="margin-top:0">Country intelligence</h3></div>', unsafe_allow_html=True)
    current_country = st.session_state.country if st.session_state.country in COUNTRY_NAMES else "United States"
    country = st.selectbox("Select a country", COUNTRY_NAMES, index=COUNTRY_NAMES.index(current_country), key="country_picker")
    dropdown_changed = country != st.session_state.get("country")
    st.session_state.country = country
    refresh_country = st.button("Generate / refresh country brief", type="primary")
    if refresh_country or (dropdown_changed and not country_cache_fresh(country)):
        with st.spinner(f"Collecting five categories for {country}…"):
            collect_country(country)

    category_tabs = st.tabs(list(CATEGORIES))
    total_articles = 0
    for tab, category in zip(category_tabs, CATEGORIES):
        with tab:
            rows = country_articles(country, category, 5); total_articles += len(rows)
            if not rows:
                st.info("No stored updates yet. Select “Generate / refresh country brief”.")
            for row in rows:
                published = row.get("published_at") or "Time unavailable"
                st.markdown(f'''<div class="article"><div class="time">{published}</div><h4>{row["headline"]}</h4>
                  <div class="sum"><b>✦ AUTOMATED SUMMARY</b><br>{row["summary"]}</div>
                  <footer>{row.get("coverage_scope", "International").upper()} · SOURCE · {row["publisher"]}<a href="{row["url"]}" target="_blank">Read original ↗</a></footer></div>''', unsafe_allow_html=True)
    if total_articles:
        st.markdown(f'<div class="brief"><b>✦ Automated country brief</b><p>{total_articles} recent reports are stored across the five intelligence categories for {country}. Review each category and open the linked reporting before operational use.</p></div>', unsafe_allow_html=True)

with right:
    selected_id = st.session_state.get("selected_signal")
    selected = next((s for s in signals if s["id"] == selected_id), signals[0] if signals else None)
    if selected:
        color = SEVERITY_COLORS.get(selected["severity"],"#64748b")
        st.markdown(f'''<div class="panel"><div class="eyebrow">EVENT INTELLIGENCE</div>
          <p style="font-size:10px;color:#6f7d90"><b>{selected["source"]}</b> · {selected["event_type"]}</p>
          <h2>{selected["title"]}</h2><p class="subtitle">⌖ {selected["location"]}</p>
          <span class="pill" style="background:{color}18;color:{color}">● {selected["severity"]} severity</span>
          <span style="float:right;font-size:10px">Confidence <b>{selected["confidence"]}%</b></span><hr style="border:0;border-top:1px solid #e5e9ef;margin:16px 0">
          <h3>✦ Automated event brief</h3><p style="font-size:11px;line-height:1.65;color:#435064">{selected["summary"]}</p>
          <div style="background:#fff6eb;border-left:3px solid #e8943d;padding:10px;border-radius:6px"><b style="color:#a86020">⚠ Outlook</b><p style="font-size:10px">{selected["outlook"]}</p></div>
          <h3>Source</h3><p style="font-size:10px">{selected["source_name"]}</p><a href="{selected["source_url"]}" target="_blank">Open primary source ↗</a></div>''', unsafe_allow_html=True)
    st.write("")
    st.markdown('<div class="panel"><h3 style="margin-top:0">Recent high-impact events</h3>', unsafe_allow_html=True)
    for item in sorted(signals, key=lambda s: ({"Critical":3,"High":2,"Watch":1}.get(s["severity"],0),s["observed_at"]), reverse=True)[:8]:
        st.markdown(f'<div class="event"><b>{item["title"]}</b><p>{item["location"]} · {item["source"]} · {item["severity"]}</p></div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

st.caption("Horizon Docker edition · SQLite persistence · Streamlit analytical and presentation layer · Verify automated summaries against linked primary sources.")
