import json
import re
from datetime import datetime, timezone

import plotly.graph_objects as go
import pycountry
import streamlit as st
from streamlit_plotly_events import plotly_events

from app.collectors import CATEGORIES, collect_all, collect_country
from app.patterns import event_match, synthesize_event_headline, synthesize_event_summary
from app.database import active_alert_count, all_country_articles, country_article_history, country_articles, country_cache_fresh, database_stats, init_db, provider_status, recent_signals
from app.supply_chain import assess_article, assess_country

st.set_page_config(page_title="OSINT Early Warning Dashboard 2.0", page_icon="◉", layout="wide", initial_sidebar_state="expanded")
init_db()

RISK_COLORS = {"Critical":"#d9363e", "Moderate":"#e5b62f", "Low":"#27a66a"}

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Manrope:wght@600;700;800&display=swap');
:root{--blue:#2567df;--ink:#182438;--muted:#718096;--line:#e1e7ee;--bg:#f4f7fb}
.stApp{background:var(--bg);color:var(--ink);font-family:'DM Sans',sans-serif}.main .block-container{max-width:1600px;padding:1.1rem 1.5rem 2rem}
[data-testid="stSidebar"]{background:#fff;border-right:1px solid var(--line)}[data-testid="stSidebar"] .block-container{padding-top:1.2rem}
h1,h2,h3{font-family:'Manrope',sans-serif!important;letter-spacing:-.02em}.brand{display:flex;align-items:center;gap:.65rem;margin:.1rem 0 1.5rem}.brandmark{width:38px;height:38px;border-radius:10px;background:linear-gradient(135deg,#2874eb,#122965);display:grid;place-items:center;color:white;font-weight:800}.brand strong{display:block;font:800 17px Manrope}.brand span{font-size:8px;color:#2b6ee8;font-weight:800;letter-spacing:1px}
.eyebrow{font-size:9px;letter-spacing:1.2px;font-weight:800;color:#64748b;margin-bottom:.15rem}.subtitle{color:#738095;font-size:13px;margin-top:-.4rem}.metric{background:#fff;border:1px solid var(--line);border-radius:10px;padding:14px 16px;box-shadow:0 2px 10px #243b5a10;min-height:96px}.metric span{font-size:9px;font-weight:800;color:#77859a;letter-spacing:.7px}.metric b{display:block;font:800 28px Manrope;margin:5px 0 1px}.metric small{color:#7d8999;font-size:9px}.panel{background:#fff;border:1px solid var(--line);border-radius:11px;padding:15px 17px;box-shadow:0 2px 12px #263b5510}.health{display:inline-flex;align-items:center;gap:5px;border:1px solid #dfe5ec;border-radius:12px;padding:4px 8px;margin-right:5px;background:#fff;font-size:9px}.health i{width:6px;height:6px;border-radius:50%;background:#20aa78}.health.unavailable i{background:#dc4b53}.event{border-bottom:1px solid #edf0f4;padding:10px 2px}.event b{font-size:12px}.event p{font-size:9px;color:#7d8999;margin:3px 0}.pill{display:inline-block;border-radius:12px;padding:3px 7px;font-size:8px;font-weight:800}.country-head{padding:10px 0;border-bottom:1px solid var(--line);margin-bottom:12px}.country-head h2{margin:0}.brief{background:#f0f5ff;border:1px solid #bfd4f7;border-left:3px solid #2869df;border-radius:8px;padding:13px 14px;margin-bottom:12px}.brief b{color:#245dbd}.brief p{font-size:11px;line-height:1.55;color:#4c607a}.article{border:1px solid #dfe5ec;background:#fcfdff;border-radius:9px;padding:12px 14px;margin:8px 0}.article .time{display:flex;align-items:center;gap:12px;margin-bottom:8px}.article .date{font-size:10px;color:#334e73;font-weight:800;letter-spacing:.25px}.article .clock{font-size:9px;color:#6f8097;font-weight:700;padding-left:12px;border-left:1px solid #ccd6e3}.article h4{font:800 13px Manrope;margin:7px 0}.article .sum{background:#f4f8ff;border-left:2px solid #5688df;padding:8px 10px;border-radius:5px;font-size:10px;color:#4b5d75}.article footer{font-size:9px;color:#64748b;margin-top:8px}.article a{color:#2868da;text-decoration:none;font-weight:700}.source-list{margin-top:9px;padding-top:8px;border-top:1px solid #e5eaf1}.source-link{display:inline-block;margin:4px 5px 0 0;padding:5px 7px;border:1px solid #dce5f2;border-radius:7px;background:#f7faff;font-size:9px}.source-link small{color:#718096;margin-left:4px}
div.stButton>button{border-radius:7px;font-weight:700;border:1px solid #dce3eb}.stTabs [data-baseweb="tab-list"]{gap:8px}.stTabs [data-baseweb="tab"]{background:#f4f6f9;border-radius:7px;padding:8px 13px}.stTabs [aria-selected="true"]{background:#eaf2ff!important;color:#2667d7!important}
.riskbox{border-radius:8px;padding:10px 11px;margin:9px 0;font-size:10px;line-height:1.5}.riskbox ul{margin:5px 0 0 18px;padding:0}.legend{display:flex;gap:14px;align-items:center;font-size:10px;color:#64748b}.legend i{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:5px}
.risk-list-row{border:1px solid #e2e8f0;border-radius:9px;padding:10px 12px;margin:7px 0;background:#fbfcfe}.risk-list-row b{font:700 13px Manrope}.risk-list-row p{margin:4px 0 0;color:#64748b;font-size:10px}
.pattern{border:1px solid #dae4f0;border-left:3px solid #7c5ce0;border-radius:8px;padding:10px 11px;margin:8px 0;background:#fbfaff}.pattern h4{font:800 11px Manrope;margin:4px 0}.pattern .meta{font-size:8px;color:#718096;line-height:1.5}.pattern .pattern-score{display:inline-block;margin:5px 5px 0 0;padding:3px 6px;border-radius:6px;background:#f0ecff;color:#6046b8;font-size:8px;font-weight:800}
.alert{border:1px solid #eadde0;border-radius:8px;padding:11px;margin:8px 0;background:#fffafb}.alert h4{font:800 11px Manrope;margin:5px 0}.alert ul{font-size:9px;color:#526176;margin:6px 0 0 16px;padding:0}.tag{display:inline-block;padding:3px 6px;border-radius:6px;background:#eef3f8;margin:3px 4px 0 0;font-size:8px;color:#43556b}
</style>
""", unsafe_allow_html=True)

with st.sidebar:
    st.markdown('<div class="brand"><div class="brandmark">OS</div><div><strong>OSINT Early Warning</strong><span>DASHBOARD 2.0</span></div></div>', unsafe_allow_html=True)
    nav = st.radio("Navigation", ["Operations overview", "Supply-chain risk map", "Country risk brief", "Alerts", "Sources", "Database"], label_visibility="collapsed")
    st.divider()
    st.caption("Docker + Streamlit")
    st.caption("Persistent SQLite storage")

if "collected" not in st.session_state:
    st.session_state.collected = False
if "country" not in st.session_state:
    st.session_state.country = "United States"
if "pending_country_refresh" not in st.session_state:
    st.session_state.pending_country_refresh = None
if "refresh_notice" not in st.session_state:
    st.session_state.refresh_notice = None
if "selected_warning_headline" not in st.session_state:
    st.session_state.selected_warning_headline = None
if "auto_refresh_country" not in st.session_state:
    st.session_state.auto_refresh_country = None
if "last_dropdown_country" not in st.session_state:
    st.session_state.last_dropdown_country = st.session_state.country


def format_article_time(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
        return f'<div class="time"><span class="date">{parsed.strftime("%d %b %Y")}</span><span class="clock">{parsed.strftime("%H:%M")} UTC</span></div>'
    except (TypeError, ValueError):
        return '<div class="time"><span class="date">Date unavailable</span><span class="clock">Time unavailable</span></div>'


def article_datetime(row):
    """Return a comparable UTC datetime, falling back to collection/observation time."""
    for field in ("published_at", "observed_at", "collected_at"):
        value = row.get(field)
        if not value:
            continue
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except (TypeError, ValueError):
            continue
    return datetime.min.replace(tzinfo=timezone.utc)


def suggested_action(article_risk, historical_matches=0):
    prefix = f"A similar issue appears in {historical_matches} stored historical report{'s' if historical_matches != 1 else ''}. " if historical_matches else ""
    if article_risk["level"] == "Critical":
        return prefix + "Escalate immediately: contact affected suppliers and logistics partners, verify routes and lead times, and activate contingency options."
    if article_risk["level"] == "Moderate":
        return prefix + "Review exposure today: confirm supplier status, transport bookings, buffer stock and alternative routing options."
    return prefix + "Monitor the issue and confirm whether the named locations, suppliers or transport routes intersect with current operations."


def headline_tokens(value):
    return set(re.findall(r"[a-z]+(?:-[a-z]+)?", (value or "").lower()))


def consolidated_country_stories(rows, country):
    """Merge same-event records across categories into stakeholder-facing stories."""
    clusters = []
    for row in sorted(rows, key=article_datetime, reverse=True):
        cluster = next((item for item in clusters if event_match(item["seed"]["headline"], row["headline"])), None)
        if cluster:
            cluster["rows"].append(row)
        else:
            clusters.append({"seed": row, "rows": [row]})
    stories = []
    for cluster in clusters:
        members = cluster["rows"]
        newest = max(members, key=article_datetime).copy()
        sources, seen = [], set()
        for member in members:
            try:
                member_sources = json.loads(member.get("sources_json") or "[]")
            except (TypeError, json.JSONDecodeError):
                member_sources = []
            for source in member_sources:
                family = source.get("source_family") or source.get("publisher")
                if family and family not in seen:
                    sources.append(source); seen.add(family)
        source_headlines = [source.get("headline") for source in sources if source.get("headline")]
        if not source_headlines:
            source_headlines = [member["headline"] for member in members]
        newest["headline"] = synthesize_event_headline(source_headlines, country)
        newest["summary"] = synthesize_event_summary(source_headlines, country)
        newest["sources_json"] = json.dumps(sources, ensure_ascii=False)
        newest["published_at"] = max((member.get("published_at") for member in members if member.get("published_at")), default=newest.get("published_at"))
        newest["country_relevance_score"] = max(int(member.get("country_relevance_score") or 0) for member in members)
        newest["country_relevance_reason"] = "Consolidated from matching local, international and operational-source headlines affecting the selected country."
        newest["risk"] = assess_article(newest)
        stories.append(newest)
    return sorted(stories, key=article_datetime, reverse=True)


def render_article(row, priority_label=False, historical_matches=0, show_action=True):
    """Render the shared canonical article record everywhere it is shown."""
    published = format_article_time(row.get("published_at"))
    article_risk = row.get("risk") or assess_article(row)
    risk_color = RISK_COLORS[article_risk["level"]]
    effects = "".join(f"<li>{effect}</li>" for effect in article_risk["effects"])
    try:
        reporting_sources = json.loads(row.get("sources_json") or "[]")
    except (TypeError, json.JSONDecodeError):
        reporting_sources = []
    if not reporting_sources:
        reporting_sources = [{"publisher": row["publisher"], "url": row["url"], "origin_country": "Origin unverified"}]
    corroborated = len({source.get("source_family") or source.get("publisher") for source in reporting_sources}) >= 2
    source_links = ""
    for source in reporting_sources:
        source_tone = source.get("tone") or {"label": "Not assessed", "score": 0}
        variance = f' · ⚠ {source["fact_variance"]}' if source.get("fact_variance") else ""
        source_links += f'<a class="source-link" href="{source["url"]}" target="_blank">{source["publisher"]}<small>🌐 {source.get("origin_country", "Origin unverified")} · {source.get("source_type", "News reporting")} · Tone {source_tone.get("label", "Not assessed")} ({source_tone.get("score", 0):+}){variance}</small></a>'
    priority = '<span class="pill" style="background:#eaf2ff;color:#2667d7">CURRENT PRIORITY</span>' if priority_label else ""
    corroboration = '<span class="pill" style="background:#e8f7ef;color:#177554">CORROBORATED</span>' if corroborated else '<span class="pill" style="background:#fff4dc;color:#9a6500">SINGLE-SOURCE / UNCONFIRMED</span>'
    provisional_note = f'<div class="riskbox" style="background:#fff8e8;border-left:3px solid #e5b62f"><b>PROVISIONAL ASSESSMENT</b><br>{article_risk["provisional_reason"]}</div>' if article_risk.get("provisional") else ""
    exposure_note = f'<div class="riskbox" style="background:#f5f8fc"><b>RISK BASIS</b><br>{article_risk.get("exposure_basis") or "General operational monitoring"}' + (f'<br>Strategic route: {", ".join(article_risk.get("strategic_routes", []))}' if article_risk.get("strategic_routes") else '') + '</div>'
    tone = article_risk.get("tone", {"label": "Neutral", "score": 0})
    relevance_note = f'<div class="riskbox" style="background:#f5f8fc"><b>COUNTRY RELEVANCE</b><br>{row.get("country_relevance_reason") or "Selected-country operational relevance verified during collection."}<br><b>Reporting tone:</b> {tone["label"]} ({tone["score"]:+}/100)</div>'
    action = f'<div class="riskbox" style="background:#f7f9fc"><b>RECOMMENDED ACTION</b><br>{suggested_action(article_risk, historical_matches)}</div>' if show_action else ""
    st.markdown(f'''<div class="article">{priority}{published}<h4>{row["headline"]}</h4>
      <div class="sum"><b>✦ AUTOMATED SUMMARY</b><br>{row["summary"]}</div>
      <div class="riskbox" style="background:{risk_color}10;border-left:3px solid {risk_color}"><b style="color:{risk_color}">{article_risk["level"]} supply-chain risk · {article_risk["mode"]}</b><br>Impact score {article_risk["score"]}/100 · Confidence {article_risk["confidence"]}% (range {article_risk["confidence_low"]}–{article_risk["confidence_high"]}%)<ul>{effects}</ul></div>
      {exposure_note}{relevance_note}{provisional_note}{action}<div class="source-list">{corroboration}<br><b style="font-size:9px">DISCOVERED MATCHING SOURCES · {len(reporting_sources)}</b><br>{source_links}</div>
      <footer>{row.get("category", "Uncategorised").upper()} · {row.get("coverage_scope", "International").upper()} COVERAGE</footer></div>''', unsafe_allow_html=True)


def stakeholder_alert_explanation(alert):
    """Translate background pattern outputs into operational language."""
    try:
        modes = json.loads(alert.get("transport_modes_json") or "[]")
    except (TypeError, json.JSONDecodeError):
        modes = []
    source_count = int(alert.get("source_count") or 0)
    active_windows = int(alert.get("active_windows") or 0)
    why = [f"Reported by {source_count} source{'s' if source_count != 1 else ''}"]
    if active_windows > 1:
        why.append(f"Remained active across {active_windows} monitoring updates")
    if modes:
        why.append(f"Potential exposure: {', '.join(modes)}")
    else:
        why.append("Potential indirect supplier, inventory or transport exposure")

    level = alert.get("alert_level") or "Watch"
    if level == "Critical":
        action = "Escalate now: contact affected suppliers and logistics partners, verify routes and lead times, and activate the relevant contingency plan."
    elif level == "Elevated":
        action = "Review exposure today: validate supplier status, transport bookings, buffer stock and alternative routing options."
    else:
        action = "Monitor closely: confirm whether named locations, suppliers or routes intersect with current operations."
    return why, action

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
    if pending_country in COUNTRY_NAMES:
        st.session_state.country = pending_country
        st.session_state.country_picker = pending_country
        st.session_state.auto_refresh_country = pending_country
        st.session_state.selected_warning_headline = None

top1, top2 = st.columns([5, 1])
with top1:
    st.markdown('<div class="eyebrow">SUPPLY CHAIN OPERATIONS / LIVE RISK VIEW</div>', unsafe_allow_html=True)
    st.title("Global supply-chain risk dashboard")
    st.markdown('<p class="subtitle">Prioritise country risk, transport exposure and corroborated disruption signals from public sources.</p>', unsafe_allow_html=True)
with top2:
    refresh = st.button("↻ Live refresh", type="primary", use_container_width=True)

if refresh or not st.session_state.collected:
    with st.spinner("Collecting GDELT, GDACS and USGS signals…"):
        collect_all()
    st.session_state.collected = True

signals = recent_signals(100); stats = database_stats(); health = provider_status()
stored_articles = all_country_articles()
articles_by_country = {}
for article in stored_articles:
    articles_by_country.setdefault(article["country"], []).append(article)
country_risks = {name: assess_country(name, articles_by_country.get(name, []), signals) for _, name in COUNTRIES}

if st.session_state.refresh_notice:
    notice = st.session_state.refresh_notice
    st.session_state.refresh_notice = None
    if notice["count"]:
        category_text = " · ".join(f'{category}: {count}' for category, count in notice["categories"].items())
        st.success(f'{notice["country"]} refreshed successfully — {notice["count"]} updates loaded. {category_text}')
    else:
        st.warning(f'No current articles were returned for {notice["country"]}. Existing cached reporting has been preserved; retry “Update news now” later.')


@st.dialog("Countries by supply-chain risk", width="large")
def show_risk_countries(level):
    color = RISK_COLORS[level]
    countries = sorted(
        (risk for risk in country_risks.values() if risk["level"] == level),
        key=lambda risk: (risk["score"], risk["confidence"]),
        reverse=True,
    )
    st.markdown(f'<h2 style="color:{color};margin-top:0">{level} risk countries</h2>', unsafe_allow_html=True)
    st.caption(f"{len(countries)} countries currently fall within this risk band. Select one to open its country brief.")
    if not countries:
        st.info(f"No countries are currently classified as {level.lower()} risk.")
        return
    for risk in countries:
        details, action = st.columns([5, 1])
        details.markdown(
            f'<div class="risk-list-row"><b>{risk["country"]}</b><p>Score {risk["score"]}/100 · {risk["mode"]} · Confidence {risk["confidence"]}% ({risk["confidence_low"]}–{risk["confidence_high"]}%) · {risk["evidence_count"]} reports/signals</p></div>',
            unsafe_allow_html=True,
        )
        if action.button("Open", key=f"open-{level}-{risk['country']}", use_container_width=True):
            st.session_state.pending_country_refresh = risk["country"]
            st.rerun()


m1,m2,m3,m4,m5,m6 = st.columns(6)
critical_count = sum(r["level"] == "Critical" for r in country_risks.values())
moderate_count = sum(r["level"] == "Moderate" for r in country_risks.values())
with m1:
    st.markdown(f'<div class="metric"><span>CRITICAL COUNTRIES</span><b>{critical_count}</b><small>immediate review</small></div>', unsafe_allow_html=True)
    if st.button("View critical countries", key="show-critical", use_container_width=True):
        show_risk_countries("Critical")
with m2:
    st.markdown(f'<div class="metric"><span>MODERATE COUNTRIES</span><b>{moderate_count}</b><small>monitor and mitigate</small></div>', unsafe_allow_html=True)
    if st.button("View moderate countries", key="show-moderate", use_container_width=True):
        show_risk_countries("Moderate")
m3.markdown(f'<div class="metric"><span>COUNTRIES MAPPED</span><b>{len(COUNTRIES)}</b><small>global operating view</small></div>', unsafe_allow_html=True)
m4.markdown(f'<div class="metric"><span>STORED ARTICLES</span><b>{stats["articles"]}</b><small>current country snapshots</small></div>', unsafe_allow_html=True)
m5.markdown(f'<div class="metric"><span>DEVELOPING RISKS</span><b>{active_alert_count(minimum_score=25)}</b><small>changes detected in the last 30 days</small></div>', unsafe_allow_html=True)
m6.markdown(f'<div class="metric"><span>PRIORITY ALERTS</span><b>{active_alert_count()}</b><small>require stakeholder review</small></div>', unsafe_allow_html=True)

st.write("")
status_html = '<div class="panel"><b style="font-size:10px;margin-right:10px">Provider health</b>'
for provider in ["GDELT","GDACS","USGS","Global news"]:
    state = health.get(provider, {}).get("status", "standby")
    status_html += f'<span class="health {state}"><i></i>{provider} · {state}</span>'
status_html += '</div>'
st.markdown(status_html, unsafe_allow_html=True)

@st.dialog("Country supply-chain intelligence", width="large")
def show_country_intelligence(country):
    if not country_cache_fresh(country, max_age_minutes=10):
        with st.spinner(f"Loading and corroborating current intelligence for {country}…"):
            collect_country(country, enrich=True)
    current_rows = sum((country_articles(country, category, 5) for category in CATEGORIES), [])
    assessment = assess_country(country, current_rows, signals)
    current = consolidated_country_stories(current_rows, country)[:5]
    history_candidates = country_article_history(country, 30)
    current_story_keys = {row.get("story_key") for row in current_rows if row.get("story_key")}
    current_headlines = {row.get("headline") for row in current_rows}
    history = []
    for row in history_candidates:
        if row.get("story_key") in current_story_keys or row.get("headline") in current_headlines:
            continue
        row["risk"] = assess_article(row)
        if row["risk"]["score"] < 22:
            continue
        history.append(row)
        if len(history) == 10:
            break
    history.sort(key=article_datetime, reverse=True)
    color = RISK_COLORS[assessment["level"]]
    escalation_note = f'<p style="font-size:10px;color:#a3212b"><b>Why this is Critical:</b> {assessment["escalation_reason"]}</p>' if assessment.get("escalation_reason") else ""
    st.markdown(f'''<div class="country-head"><div class="eyebrow">COUNTRY SUPPLY-CHAIN INTELLIGENCE</div><h2>{country}</h2>
      <span class="pill" style="background:{color}18;color:{color}">● {assessment["level"]} operational risk · {assessment["score"]}/100</span>
      <span class="pill">Confidence {assessment["confidence"]}%</span><p class="subtitle">{assessment["summary"]}</p>{escalation_note}</div>''', unsafe_allow_html=True)
    current_tab, historical_tab = st.tabs(["Current trend", "Historical trend"])
    historical_keys = [headline_tokens(row.get("headline", "")) for row in history]
    with current_tab:
        st.caption("The five latest operationally relevant stories. Risk considers maritime, aviation and other transport disruption; corroboration requires two or more independent source families.")
        if not current:
            st.info("No current supply-chain reporting was returned for this country.")
        for row in current:
            tokens = headline_tokens(row.get("headline", ""))
            matches = sum(len(tokens & old) >= 3 for old in historical_keys)
            render_article(row, priority_label=True, historical_matches=matches, show_action=True)
        if st.button("Update current intelligence", type="primary", use_container_width=True, key=f"dialog-refresh-{country}"):
            with st.spinner("Running expanded source and corroboration checks…"):
                collect_country(country, enrich=True)
            st.rerun()
    with historical_tab:
        st.caption("The ten latest distinct supply-chain stories retained from previous collection windows and used as context for current recommendations.")
        if not history:
            st.info("Historical intelligence will build automatically as this country is refreshed over time.")
        for row in history:
            row["summary"] = row.get("summary") or f'{row.get("publisher") or "A reporting source"} reported: {row["headline"]}.'
            render_article(row, show_action=False)


st.markdown('''<div class="panel"><h3 style="margin:0">Global supply-chain intelligence map</h3><p class="subtitle">Hover to identify a country. Click anywhere inside a country to open its current and historical supply-chain intelligence.</p><div class="legend"><span><i style="background:#27a66a"></i>Low</span><span><i style="background:#e5b62f"></i>Moderate</span><span><i style="background:#d9363e"></i>Critical</span></div></div>''', unsafe_allow_html=True)
selector_col, open_col = st.columns([5, 1])
with selector_col:
    selected_country = st.selectbox(
        "Search for a country",
        COUNTRY_NAMES,
        index=None,
        placeholder="Type a country name…",
        key="map_country_selector",
    )
with open_col:
    st.write("")
    open_selected_country = st.button(
        "Open intelligence", type="primary", use_container_width=True,
        disabled=selected_country is None,
    )
dropdown_country_changed = selected_country is not None and selected_country != st.session_state.last_dropdown_country
if dropdown_country_changed:
    st.session_state.last_dropdown_country = selected_country
if selected_country and (open_selected_country or dropdown_country_changed):
    st.session_state.country = selected_country
    show_country_intelligence(selected_country)

fig = go.Figure(go.Choropleth(
    locations=[code for code, _ in COUNTRIES], locationmode="ISO-3",
    z=[{"Low": 0, "Moderate": 1, "Critical": 2}[country_risks[name]["level"]] for _, name in COUNTRIES],
    customdata=[[name, country_risks[name]["level"], country_risks[name]["score"], country_risks[name]["confidence"]]
                for _, name in COUNTRIES],
    zmin=0, zmax=2,
    colorscale=[[0, "#27a66a"], [0.4999, "#27a66a"], [0.5, "#e5b62f"],
                [0.7499, "#e5b62f"], [0.75, "#d9363e"], [1, "#d9363e"]],
    showscale=False,
    marker_line_color="#8faab4", marker_line_width=0.6,
    hovertemplate="<b>%{customdata[0]}</b><br>%{customdata[1]} risk · %{customdata[2]}/100<br>Confidence %{customdata[3]}%<br>Click for intelligence<extra></extra>",
))
fig.update_geos(showcountries=True, countrycolor="#91aab2", showcoastlines=True,
    coastlinecolor="#89a6b2", showland=True, landcolor="#dce8e8", showocean=True, oceancolor="#d8edf3",
    projection_type="natural earth")
fig.update_layout(height=760, margin=dict(l=0,r=0,t=0,b=0), paper_bgcolor="white", geo_bgcolor="#d8edf3", clickmode="event+select")
clicks = plotly_events(fig, click_event=True, hover_event=False, select_event=False, override_height=760, key="country-map")
if clicks:
    try:
        clicked_country = fig.data[0].customdata[clicks[0].get("pointNumber", 0)][0]
        st.session_state.country = clicked_country
        show_country_intelligence(clicked_country)
    except (IndexError, TypeError):
        pass

st.caption("OSINT Early Warning Dashboard 2.0 · Supply-chain decision support · Docker deployment · SQLite persistence · Verify automated assessments against linked primary sources.")
