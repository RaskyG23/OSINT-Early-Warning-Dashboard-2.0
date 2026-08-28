import html
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import plotly.graph_objects as go
import pycountry
import streamlit as st
import streamlit.components.v1 as components
from streamlit_plotly_events import plotly_events

from app.collectors import (CATEGORIES, collect_all, collect_country,
                            collect_country_map_snapshot, collect_gdacs,
                            collect_flight_routes, collect_general_news, collect_opensky,
                            collect_vessel_activity, country_maritime_tracker_urls)
from app.patterns import event_match, synthesize_event_headline, synthesize_event_summary
from app.database import (active_alert_count, all_country_articles, clear_profile_feedback,
                          country_article_history, country_articles, country_cache_fresh,
                          country_pattern_anomaly_scores, current_gdacs_signals,
                          database_stats, feedback_for_articles, init_db, profile_feedback,
                          profile_country_exposures,
                          provider_status, latest_aircraft, latest_vessels, recent_signals,
                          save_article_feedback, save_country_exposure)
from app.recommender import article_key, rank_articles, recommendation_reason
from app.embeddings import train_ppmi_embeddings
from app.news_taxonomy import classify_general_news
from app.supply_chain import (assess_article, assess_country, country_sentiment,
                              country_mentioned, country_supply_chain_relevance,
                              set_embedding_model)
from app.transport import (active_records, age_minutes, flight_estimates, format_duration,
                           cargo_flight_assessment, country_relationship, likely_commercial_aircraft,
                           likely_commercial_vessel,
                           resolve_aircraft_click, resolve_vessel_click, route_path)
from app.disasters import ALERT_COLOURS, HAZARD_LABELS, disaster_details
from app.forecasting import forecast_country

st.set_page_config(page_title="OSINT Early Warning Dashboard 2.0", page_icon="◉", layout="wide", initial_sidebar_state="expanded")
init_db()

RISK_COLORS = {"Critical":"#d9363e", "Moderate":"#e5b62f", "Low":"#27a66a"}
HOST_MODE = os.getenv("OSINT_HOST_MODE", "Docker + Streamlit")

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
    nav = st.radio("Navigation", ["Operations overview", "Supply-chain risk map", "Company exposure", "Live transport monitor", "Disaster monitor", "Country risk brief"], label_visibility="collapsed")
    st.divider()
    st.markdown("**Personalisation profile**")
    active_profile = st.text_input("Profile name", value="Supply Chain Manager", key="active_profile",
                                   help="Use a different name for each person or role.")
    feedback_count = len(profile_feedback(active_profile.strip() or "Supply Chain Manager"))
    st.caption(f"{feedback_count} saved preference{'s' if feedback_count != 1 else ''}")
    st.caption(HOST_MODE)
    if HOST_MODE == "Browser-hosted Streamlit":
        st.caption("Examiner demo: SQLite history may reset after service restarts")
    else:
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
if "transport_auto_attempts" not in st.session_state:
    st.session_state.transport_auto_attempts = {}
if "selected_vessel_mmsi" not in st.session_state:
    st.session_state.selected_vessel_mmsi = None
if "disaster_auto_attempt" not in st.session_state:
    st.session_state.disaster_auto_attempt = 0.0
if "selected_disaster_id" not in st.session_state:
    st.session_state.selected_disaster_id = None
if "country_map_epoch" not in st.session_state:
    st.session_state.country_map_epoch = 0


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


def record_preference(profile, key, article, feedback, transport_mode, reopen_dialog=False):
    """Button callback: save before Streamlit redraws the current page/dialog."""
    save_article_feedback(profile, key, {**article, "transport_mode": transport_mode}, feedback)
    if reopen_dialog and article.get("country"):
        # A widget callback reruns the script. Remember the dialog for that one
        # rerun so rating an article does not return the analyst to the map.
        st.session_state.reopen_country_dialog = article["country"]


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


def country_decision_candidates(rows, country, limit=15, max_age_days=7, allow_context_fallback=False):
    """Return a recent operational candidate pool, excluding incidental mentions."""
    # The current-risk surface must not reuse old SQLite rows indefinitely.
    # Reports older than seven days remain available to the Historical trend,
    # but cannot classify a country's present operational state as Moderate or
    # Critical.
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    relevant = [
        row for row in rows
        if article_datetime(row) >= cutoff
        and country_supply_chain_relevance(row, country)["relevant"]
    ]
    consolidated = consolidated_country_stories(relevant, country)
    hazard_terms = {"earthquake", "tsunami", "flood", "wildfire", "cyclone", "hurricane",
                    "typhoon", "volcano", "eruption", "drought", "landslide"}

    def decision_relevant(story):
        risk = story.get("risk") or assess_article(story)
        story_text = f'{story.get("headline", "")} {story.get("summary", "")}'
        words = headline_tokens(story_text)
        operational_evidence = bool(
            risk["operational_terms"] or risk["inferred_route_exposure"]
            or risk["primary_source_count"] >= 1
        )
        metaphorical_hazard = any(phrase in story_text.casefold() for phrase in (
            "economic tsunami", "political earthquake", "electoral earthquake", "storm of criticism"
        ))
        supported_hazard = bool(words & hazard_terms) and risk["source_count"] >= 2 and not metaphorical_hazard
        return risk["level"] in {"Moderate", "Critical"} or operational_evidence or supported_hazard

    # Exclude political/general mentions that happen to contain the country
    # name but provide no supply-chain consequence from operational scoring.
    decision_updates = [story for story in consolidated if decision_relevant(story)]
    if allow_context_fallback and len(decision_updates) < limit:
        # The readable brief may use remaining country-verified, supply-chain
        # context to fill its five stakeholder slots. This fallback is never
        # enabled for the seven-day country-risk scoring window.
        selected_ids = {story.get("story_key") or story.get("url") or story.get("headline") for story in decision_updates}
        for story in consolidated:
            identity = story.get("story_key") or story.get("url") or story.get("headline")
            if identity in selected_ids:
                continue
            decision_updates.append(story)
            selected_ids.add(identity)
            if len(decision_updates) >= limit:
                break
    return sorted(decision_updates, key=article_datetime, reverse=True)[:limit]


def selected_country_updates(rows, country, feedback_rows=None, limit=5):
    """Select a personalised operational set, then present it in time order."""
    # The stakeholder brief mirrors the collectors' rolling 30-day window so
    # it can still present the latest qualifying context when a country has no
    # event in the much stricter seven-day risk window. These older display
    # items never enter the current country-risk calculation below.
    candidates = country_decision_candidates(
        rows, country, max(limit * 3, 15), max_age_days=30, allow_context_fallback=True
    )
    selected = rank_articles(candidates, feedback_rows or [])[:limit]
    selected.sort(key=article_datetime, reverse=True)
    return selected


def canonical_country_updates(rows, country, limit=5):
    """Backward-compatible unpersonalised operational selection."""
    return selected_country_updates(rows, country, [], limit)


def latest_country_scoring_events(rows, country, limit=10):
    """Latest distinct operational events used by risk, independent of interests."""
    return country_decision_candidates(rows, country, limit, max_age_days=7)[:limit]


def render_article(row, priority_label=False, historical_matches=0, show_action=True,
                   profile=None, feedback_rows=None, show_recommendation=False):
    """Render the shared canonical article record everywhere it is shown."""
    published = format_article_time(row.get("published_at"))
    # Recompute here with the active profile's exposure so the displayed
    # operational connection matches the country-level hybrid assessment.
    article_risk = assess_article(row, company_exposures.get(row.get("country")))
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
        source_tone = country_sentiment(source.get("headline") or row.get("headline", ""), row.get("country", ""))
        variance = f' · ⚠ {source["fact_variance"]}' if source.get("fact_variance") else ""
        source_links += f'<a class="source-link" href="{source["url"]}" target="_blank">{source["publisher"]}<small>🌐 {source.get("origin_country", "Origin unverified")} · {source.get("source_type", "News reporting")} · Country sentiment {source_tone.get("label", "Neutral")} ({source_tone.get("score", 0):+}/100){variance}</small></a>'
    priority = '<span class="pill" style="background:#eaf2ff;color:#2667d7">CURRENT PRIORITY</span>' if priority_label else ""
    recommendation = ""
    if show_recommendation and row.get("recommendation_score") is not None:
        recommendation = (f'<span class="pill" style="background:#f1edff;color:#6546bd">'
                          f'RECOMMENDED FOR YOU · {row["recommendation_score"]}%</span>')
        if row.get("recommendation_trained"):
            recommendation += f'<p style="font-size:9px;color:#64748b">{recommendation_reason(row, feedback_rows or [])}</p>'
    corroboration = '<span class="pill" style="background:#e8f7ef;color:#177554">CORROBORATED</span>' if corroborated else '<span class="pill" style="background:#fff4dc;color:#9a6500">SINGLE-SOURCE / UNCONFIRMED</span>'
    hazard_evidence = ""
    if row.get("category") == "Environment and hazards":
        official = sorted({source.get("publisher") for source in reporting_sources
                           if source.get("source_family") in {"USGS", "GDACS"}})
        news_families = {source.get("source_family") or source.get("publisher")
                         for source in reporting_sources
                         if source.get("source_family") not in {"USGS", "GDACS"}}
        gdelt_match = any(source.get("indexed_by") == "GDELT" or
                          source.get("source_type") == "GDELT-indexed news"
                          for source in reporting_sources)
        if official and news_families:
            status = "OFFICIALLY DETECTED + NEWS CORROBORATED"
            detail = f'Official feed: {", ".join(official)} · Independent reporting families: {len(news_families)}'
            colour = "#177554"
        elif official:
            status = "OFFICIAL HAZARD DETECTION"
            detail = f'Official feed: {", ".join(official)} · Matching news coverage is still being sought'
            colour = "#2667d7"
        elif len(news_families) >= 2:
            status = "MULTI-SOURCE NEWS CORROBORATION"
            detail = f'{len(news_families)} reporting families matched; no applicable USGS/GDACS detection is stored'
            colour = "#9a6500"
        else:
            status = "UNCONFIRMED HAZARD REPORT"
            detail = "No applicable official detection or second independent reporting family is stored"
            colour = "#9a6500"
        if gdelt_match:
            detail += " · GDELT-indexed coverage matched"
        hazard_evidence = (f'<div class="riskbox" style="background:{colour}10;border-left:3px solid {colour}">'
                           f'<b style="color:{colour}">{status}</b><br>{detail}</div>')
    provisional_note = f'<div class="riskbox" style="background:#fff8e8;border-left:3px solid #e5b62f"><b>PROVISIONAL ASSESSMENT</b><br>{article_risk["provisional_reason"]}</div>' if article_risk.get("provisional") else ""
    connection = article_risk.get("operational_connection") or {}
    connection_note = ""
    if connection:
        components = connection.get("components", {})
        labels = {"asset_location": "Asset/location", "consequence": "Confirmed consequence",
                  "company_dependency": "Company dependency", "source_support": "Source confirmation",
                  "temporal_relevance": "Time relevance"}
        detail = " · ".join(f'{labels.get(key, key)} {value}/100' for key, value in components.items())
        assets = ", ".join(connection.get("assets") or []) or "not specifically identified"
        locations = ", ".join(connection.get("locations") or []) or "not specifically identified"
        consequences = ", ".join(connection.get("consequences") or []) or "not confirmed"
        gate = "Evidence gate passed" if connection.get("gate_passed") else "Evidence gate not passed: " + ", ".join(connection.get("missing_evidence") or [])
        connection_colour = "#177554" if connection.get("gate_passed") else "#9a6500"
        connection_note = (f'<div class="riskbox" style="background:{connection_colour}0d;border-left:3px solid {connection_colour}">'
                           f'<b style="color:{connection_colour}">OPERATIONAL CONNECTION · {connection.get("strength", "Weak")} {connection.get("score", 0)}/100</b><br>'
                           f'{detail}<br><b>Assets:</b> {assets}<br><b>Locations/routes:</b> {locations}<br>'
                           f'<b>Consequences:</b> {consequences}<br><b>Status:</b> {connection.get("lifecycle", "Unconfirmed")}<br>{gate}</div>')
    action = f'<div class="riskbox" style="background:#f7f9fc"><b>RECOMMENDED ACTION</b><br>{suggested_action(article_risk, historical_matches)}</div>' if show_action else ""
    st.markdown(f'''<div class="article">{priority}{recommendation}{published}<h4>{row["headline"]}</h4>
      <div class="sum"><b>✦ AUTOMATED SUMMARY</b><br>{row["summary"]}</div>
      <div class="riskbox" style="background:{risk_color}10;border-left:3px solid {risk_color}"><b style="color:{risk_color}">{article_risk["level"]} supply-chain risk · {article_risk["mode"]}</b><br>Impact score {article_risk["score"]}/100 · Confidence {article_risk["confidence"]}% (range {article_risk["confidence_low"]}–{article_risk["confidence_high"]}%)<ul>{effects}</ul></div>
      {hazard_evidence}{connection_note}{provisional_note}{action}<div class="source-list">{corroboration}<br><b style="font-size:9px">DISCOVERED MATCHING SOURCES · {len(reporting_sources)}</b><br>{source_links}</div>
      <footer>{row.get("category", "Uncategorised").upper()} · {row.get("coverage_scope", "International").upper()} COVERAGE</footer></div>''', unsafe_allow_html=True)
    if profile and show_recommendation:
        key = article_key(row)
        existing = feedback_for_articles(profile, [key]).get(key)
        like_col, dislike_col, status_col = st.columns([1, 1, 4])
        like_col.button(
            "👍 Interested", key=f"like-{profile}-{key}",
            type="primary" if existing == 1 else "secondary",
            on_click=record_preference, args=(profile, key, row, 1, article_risk["mode"], True),
        )
        dislike_col.button(
            "👎 Not interested", key=f"dislike-{profile}-{key}",
            type="primary" if existing == -1 else "secondary",
            on_click=record_preference, args=(profile, key, row, -1, article_risk["mode"], True),
        )
        status_col.caption("Saved as interested" if existing == 1 else "Saved as not interested" if existing == -1 else "Rate this story to improve future country rankings.")


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
        st.session_state.selected_warning_headline = None
        # The country brief function is defined later in the script. Preserve
        # this request until then instead of consuming it without opening.
        st.session_state.open_country_brief = pending_country

top1, top2 = st.columns([5, 1])
with top1:
    st.markdown('<div class="eyebrow">SUPPLY CHAIN OPERATIONS / LIVE RISK VIEW</div>', unsafe_allow_html=True)
    st.title("Global supply-chain risk dashboard")
    st.markdown('<p class="subtitle">Prioritise country risk, transport exposure and corroborated disruption signals from public sources.</p>', unsafe_allow_html=True)
with top2:
    refresh = st.button("↻ Live refresh", type="primary", use_container_width=True)

if not st.session_state.collected:
    with st.spinner("Collecting GDELT, GDACS and USGS signals…"):
        collect_all()
    st.session_state.collected = True


def map_refresh_targets(signal_rows, stored_rows, exposure_countries):
    """Return every country with current evidence that can change map risk.

    The former fixed limit left some evidenced countries stale until their
    briefs were opened.  Restricting stored rows to the seven-day scoring
    window keeps this refresh bounded without arbitrarily dropping countries.
    """
    priority = {}
    selected = st.session_state.get("country")
    if selected in COUNTRY_NAMES:
        priority[selected] = 1000
    for country in exposure_countries:
        if country in COUNTRY_NAMES:
            priority[country] = max(priority.get(country, 0), 800)
    severity_priority = {"Critical": 750, "High": 650, "Watch": 550}
    for signal in signal_rows:
        signal_place = f'{signal.get("country", "")} {signal.get("location", "")}'.strip()
        # Feed geography is not standardised: examples include "Russian
        # Federation", "Moscow, ..., Russia", "USA" and official UN-style
        # names. Use the same controlled alias rules as country intelligence,
        # preferring the most specific matching country when several names are
        # present (for example Georgia, United States).
        matches = [name for name in COUNTRY_NAMES if country_mentioned(signal_place, name)]
        match = max(matches, key=len) if matches else None
        if match:
            priority[match] = max(priority.get(match, 0), severity_priority.get(signal.get("severity"), 500))
    # Countries already represented by fresh operational articles must also be
    # refreshed; otherwise their colour can remain stale until clicked.
    scoring_cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    for row in stored_rows:
        country = row.get("country")
        if country in COUNTRY_NAMES and article_datetime(row) >= scoring_cutoff:
            article_priority = 400 + min(100, assess_article(row).get("score", 0))
            priority[country] = max(priority.get(country, 0), article_priority)
    ordered = sorted(priority, key=lambda country: (-priority[country], country))
    return ordered


if refresh:
    with st.spinner("Refreshing global feeds and current country risk snapshots…"):
        collect_all()
        refresh_signals = recent_signals(200)
        refresh_stored = all_country_articles()
        refresh_profile = (st.session_state.get("active_profile") or "Supply Chain Manager").strip()
        refresh_exposures = profile_country_exposures(refresh_profile)
        targets = map_refresh_targets(refresh_signals, refresh_stored, refresh_exposures)
        refreshed, returned = 0, 0
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = {executor.submit(collect_country_map_snapshot, country): country for country in targets}
            for future in as_completed(futures):
                try:
                    rows = future.result()
                    refreshed += 1
                    returned += len(rows)
                except Exception:
                    continue
        st.session_state.map_refresh_notice = {"countries": refreshed, "articles": returned}
    st.session_state.collected = True

signals = recent_signals(100); stats = database_stats(); health = provider_status()
stored_articles = all_country_articles()

map_refresh_notice = st.session_state.pop("map_refresh_notice", None)
if map_refresh_notice:
    st.success(
        f'Map risk refreshed before selection: {map_refresh_notice["countries"]} countries with current evidence checked and '
        f'{map_refresh_notice["articles"]} operational reports stored. All country colours below were then recalculated.'
    )


@st.cache_resource(show_spinner=False)
def cached_embedding_model(corpus):
    return train_ppmi_embeddings(corpus)


embedding_corpus = tuple(f'{row.get("headline", "")} {row.get("summary", "")}' for row in stored_articles)
embedding_model = cached_embedding_model(embedding_corpus)
set_embedding_model(embedding_model)
active_profile_name = (st.session_state.get("active_profile") or "Supply Chain Manager").strip()
active_preferences = profile_feedback(active_profile_name)
company_exposures = profile_country_exposures(active_profile_name)
pattern_anomalies = country_pattern_anomaly_scores()
articles_by_country = {}
for article in stored_articles:
    articles_by_country.setdefault(article["country"], []).append(article)
country_updates = {
    name: selected_country_updates(articles_by_country.get(name, []), name, active_preferences)
    for _, name in COUNTRIES
}
country_scoring_events = {
    name: latest_country_scoring_events(articles_by_country.get(name, []), name, 10)
    for _, name in COUNTRIES
}
country_risks = {
    name: assess_country(name, country_scoring_events[name], signals,
                         exposure=company_exposures.get(name), anomaly_score=pattern_anomalies.get(name, 0))
    for _, name in COUNTRIES
}


def assess_profile_country(country, articles):
    return assess_country(country, articles, signals, exposure=company_exposures.get(country),
                          anomaly_score=pattern_anomalies.get(country, 0))


def show_company_exposure():
    st.markdown('<div class="eyebrow">STAKEHOLDER-SPECIFIC RISK CONTEXT</div>', unsafe_allow_html=True)
    st.header("Company exposure")
    st.caption("Tell the hybrid model how dependent your operation is on a country. These values affect risk only when current evidence exists; exposure alone cannot create an alert.")
    names = [name for _, name in COUNTRIES]
    default_country = st.session_state.get("country", "United States")
    selected = st.selectbox("Country", names, index=names.index(default_country) if default_country in names else 0,
                            key="exposure_country", help="Type to search, then select a country.")
    existing = company_exposures.get(selected, {})
    labels = {
        "supplier_concentration": ("Supplier concentration", "How much sourcing depends on suppliers in this country."),
        "goods_value": ("Value of goods exposed", "Relative financial value of goods sourced, stored or sold here."),
        "route_dependency": ("Transport-route dependency", "Dependence on this country's ports, airports, roads or corridors."),
        "inventory_vulnerability": ("Inventory vulnerability", "Sensitivity to delay because safety stock or lead-time protection is limited."),
        "customer_exposure": ("Customer exposure", "Importance of customers and demand in this country."),
        "substitution_difficulty": ("Substitution difficulty", "Difficulty of replacing suppliers, routes, products or capacity."),
    }
    values = {}
    with st.form(f"exposure-form-{selected}"):
        left, right = st.columns(2)
        for index, (field, (label, help_text)) in enumerate(labels.items()):
            column = left if index % 2 == 0 else right
            values[field] = column.slider(label, 0, 100, int(existing.get(field, 0) or 0), 5,
                                          help=help_text, key=f"exposure-{selected}-{field}")
        submitted = st.form_submit_button("Save company exposure", type="primary", use_container_width=True)
    preview = round(sum(values[field] * weight for field, weight in {
        "supplier_concentration": .25, "goods_value": .20, "route_dependency": .20,
        "inventory_vulnerability": .15, "customer_exposure": .10, "substitution_difficulty": .10,
    }.items()))
    st.info(f"Current weighted company-exposure index for {selected}: **{preview}/100**")
    if submitted:
        save_country_exposure(active_profile_name, selected, values)
        st.session_state.country = selected
        st.success("Exposure saved. Recalculating the map and country assessment…")
        st.rerun()


if nav == "Company exposure":
    show_company_exposure()
    st.stop()


@st.cache_data(ttl=600, show_spinner=False)
def cached_general_news(country):
    return collect_general_news(country, limit=10)

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


def show_interest_training():
    profile = (st.session_state.get("active_profile") or "Supply Chain Manager").strip()
    preferences = profile_feedback(profile)
    st.markdown('<div class="panel"><h3 style="margin:0">Teach the dashboard what matters to you</h3>'
                '<p class="subtitle">Rate a varied set of stored supply-chain stories. Your choices personalise rankings inside every country brief; they do not change evidence, risk or confidence scores.</p></div>',
                unsafe_allow_html=True)
    positive = sum(item["feedback"] == 1 for item in preferences)
    negative = sum(item["feedback"] == -1 for item in preferences)
    a, b, c = st.columns([1, 1, 4])
    a.metric("Interested", positive)
    b.metric("Not interested", negative)
    if c.button("Reset this profile", disabled=not preferences, key=f"reset-profile-{profile}"):
        clear_profile_feedback(profile)
        st.rerun()

    # Interleave countries so the training set is varied rather than dominated
    # by whichever country was refreshed most recently.
    candidates, country_counts, seen = [], {}, set()
    for article in sorted(stored_articles, key=article_datetime, reverse=True):
        key = article_key(article)
        country = article.get("country") or "Unknown"
        if key in seen or country_counts.get(country, 0) >= 2:
            continue
        enriched = article.copy()
        enriched["risk"] = assess_article(enriched)
        candidates.append(enriched)
        country_counts[country] = country_counts.get(country, 0) + 1
        seen.add(key)
        if len(candidates) == 12:
            break
    if not candidates:
        st.info("Refresh several countries first; their stories will appear here for preference training.")
        return
    st.caption("Choose both Interested and Not interested. Six or more varied ratings usually provide a useful first hybrid profile; older choices gradually carry less weight.")
    for article in candidates:
        st.markdown(f'**{article.get("headline", "Untitled")}**  \n'
                    f'{article.get("country", "Unknown country")} · {article["risk"]["mode"]} · '
                    f'{article["risk"]["level"]} operational risk')
        key = article_key(article)
        current_feedback = feedback_for_articles(profile, [key]).get(key)
        like, dislike, state = st.columns([1, 1, 4])
        like.button(
            "👍 Interested", key=f"train-like-{profile}-{key}",
            type="primary" if current_feedback == 1 else "secondary",
            on_click=record_preference,
            args=(profile, key, article, 1, article["risk"]["mode"]),
        )
        dislike.button(
            "👎 Not interested", key=f"train-dislike-{profile}-{key}",
            type="primary" if current_feedback == -1 else "secondary",
            on_click=record_preference,
            args=(profile, key, article, -1, article["risk"]["mode"]),
        )
        state.caption("Preference saved" if current_feedback else "Not rated")
        st.divider()


if nav == "My news interests":
    show_interest_training()
    st.stop()


def show_transport_monitor():
    st.markdown('<div class="panel"><h3 style="margin:0">Live commercial transport monitor</h3>'
                '<p class="subtitle">Recent OpenSky aircraft and hybrid open maritime intelligence from official regional AIS feeds and Global Fishing Watch enrichment. Monitoring evidence does not alter country risk scores.</p></div>',
                unsafe_allow_html=True)
    country = st.selectbox("Monitoring area", COUNTRY_NAMES, index=COUNTRY_NAMES.index(st.session_state.country)
                           if st.session_state.country in COUNTRY_NAMES else 0, key="transport-country")
    # Transport observations expire after 30 minutes. Collect automatically on
    # first opening (and after expiry) so the page never depends on the user
    # discovering and pressing Refresh before anything can be displayed.
    stored_aircraft = latest_aircraft(country, 500)
    stored_vessels = latest_vessels(500, country)
    has_fresh_aircraft = bool(active_records(stored_aircraft, 30))
    has_fresh_vessels = bool(active_records(stored_vessels, 30))
    last_attempt = st.session_state.transport_auto_attempts.get(country, 0)
    automatic_refresh = (not has_fresh_aircraft and not has_fresh_vessels
                         and datetime.now(timezone.utc).timestamp() - last_attempt >= 300)
    manual_refresh = st.button("Refresh live transport activity", type="primary", use_container_width=True)
    if manual_refresh or automatic_refresh:
        st.session_state.transport_auto_attempts[country] = datetime.now(timezone.utc).timestamp()
        with st.spinner(f"Collecting current aircraft and vessel positions around {country}…"):
            aircraft_result = collect_opensky(country)
            vessel_result = collect_vessel_activity(country=country, duration_seconds=20)
        if not aircraft_result and not vessel_result:
            st.warning("No new positions were returned. Check the provider messages below; zero does not mean there is no traffic.")

    aircraft_all = latest_aircraft(country, 500)
    vessels_all = latest_vessels(500, country)
    aircraft_recent = active_records(aircraft_all, 30)
    vessels_recent = active_records(vessels_all, 30)
    commercial_aircraft = [item for item in aircraft_recent if likely_commercial_aircraft(item)]
    commercial_vessels = [item for item in vessels_recent if likely_commercial_vessel(item)]
    routes = collect_flight_routes(commercial_aircraft)
    for item in commercial_aircraft:
        callsign = (item.get("callsign") or "").strip().upper()
        item["route"] = routes.get(callsign) or {}
        item["estimate"] = flight_estimates(item, item["route"])
        item["country_relationship"] = country_relationship(item["route"], country, position_in_country=True)
        item["cargo"] = cargo_flight_assessment(item, item["route"])
    cargo_aircraft = [item for item in commercial_aircraft if item["cargo"]["cargo"]]
    excluded_aircraft = len(commercial_aircraft) - len(cargo_aircraft)
    commercial_aircraft = cargo_aircraft
    tm1, tm2, tm3, tm4 = st.columns(4)
    tm1.metric("Likely dedicated cargo flights", len(commercial_aircraft), help="Recognised freight-operator callsign or cargo-airline route metadata. This does not reveal the actual manifest.")
    tm2.metric("Likely commercial vessels", len(commercial_vessels), help="Commercial AIS class where available, otherwise named vessels moving at 0.5 knots or more.")
    tm3.metric("Other aircraft excluded", excluded_aircraft, help="Passenger, private or otherwise unverified aircraft are not shown as cargo flights.")
    tm4.metric("Unclassified vessels", max(0, len(vessels_recent) - len(commercial_vessels)))

    transport_fig = go.Figure()
    aircraft_curve = None
    vessel_curve = None
    if commercial_aircraft:
        aircraft_curve = len(transport_fig.data)
        transport_fig.add_trace(go.Scattergeo(
            lat=[item["latitude"] for item in commercial_aircraft], lon=[item["longitude"] for item in commercial_aircraft],
            text=[f'{item.get("callsign") or item["icao24"]}<br>{item["country_relationship"]}<br>'
                  + (f'From: {item["route"].get("origin_iata")}<br>' if item["route"].get("origin_iata") else '')
                  + (f'To: {item["route"].get("destination_iata")}<br>' if item["route"].get("destination_iata") else '')
                  + f'Altitude: {round(float(item.get("altitude_m") or 0)):,} m<br>Speed: {round(float(item.get("velocity_knots") or 0))} kt<br>Age: {round(age_minutes(item))} min' for item in commercial_aircraft],
            hovertemplate="%{text}<extra></extra>", mode="markers", name="Cargo flights",
            customdata=[item["icao24"] for item in commercial_aircraft],
            marker=dict(size=8, color="#2567df", symbol="triangle-up", line=dict(width=.5, color="white")),
        ))
    if commercial_vessels:
        vessel_curve = len(transport_fig.data)
        selected_mmsi = str(st.session_state.get("selected_vessel_mmsi") or "")
        transport_fig.add_trace(go.Scattergeo(
            lat=[item["latitude"] for item in commercial_vessels], lon=[item["longitude"] for item in commercial_vessels],
            text=[f'{item.get("ship_name") or item["mmsi"]}<br>Type: {item.get("vessel_type") or "Inferred commercial"}<br>Speed: {float(item.get("speed_knots") or 0):.1f} kt<br>Course: {round(float(item.get("course") or 0))}°<br>Age: {round(age_minutes(item))} min' for item in commercial_vessels],
            hovertemplate="%{text}<extra></extra>", mode="markers", name="Likely commercial vessel",
            customdata=[str(item["mmsi"]) for item in commercial_vessels],
            marker=dict(
                size=[13 if str(item.get("mmsi")) == selected_mmsi else 8 for item in commercial_vessels],
                color=["#f08c22" if str(item.get("mmsi")) == selected_mmsi else "#12a676"
                       for item in commercial_vessels],
                symbol="diamond", line=dict(width=.7, color="white"),
            ),
        ))
    # Keep aircraft markers as curve 0 for click handling. Route traces are
    # appended afterwards and use origin -> live position -> destination.
    for item in commercial_aircraft:
        path = route_path(item, item["route"])
        if not path:
            continue
        is_selected = item.get("icao24") == st.session_state.get("selected_flight_icao")
        transport_fig.add_trace(go.Scattergeo(
            lat=path["latitudes"], lon=path["longitudes"], mode="lines",
            line=dict(width=3 if is_selected else 1, color="#163f9e" if is_selected else "#7ca2e8"),
            opacity=1 if is_selected else .45,
            hoverinfo="skip", showlegend=False,
        ))
    transport_fig.update_geos(showcountries=True, countrycolor="#91aab2", showcoastlines=True,
        coastlinecolor="#89a6b2", showland=True, landcolor="#e6eeee", showocean=True,
        oceancolor="#d8edf3", projection_type="natural earth", fitbounds="locations" if commercial_aircraft or commercial_vessels else None)
    transport_fig.update_layout(height=650, margin=dict(l=0, r=0, t=20, b=0), paper_bgcolor="white",
                                geo_bgcolor="#d8edf3", legend=dict(orientation="h"))
    transport_clicks = plotly_events(
        transport_fig, click_event=True, hover_event=False, select_event=False,
        override_height=650, key=f"transport-map-{country}",
    )
    if transport_clicks:
        clicked = transport_clicks[0]
        # plotly-events versions differ between pointNumber and pointIndex, and
        # some omit curveNumber for a single trace. Accept all three cases.
        curve_number = clicked.get("curveNumber")
        custom = str(clicked.get("customdata") or "").strip()
        selected_aircraft = next(
            (item for item in commercial_aircraft if str(item.get("icao24") or "") == custom), None
        ) if custom else None
        selected_vessel = next(
            (item for item in commercial_vessels if str(item.get("mmsi") or "") == custom), None
        ) if custom else None
        if not selected_aircraft and not selected_vessel and curve_number == aircraft_curve:
            selected_aircraft = resolve_aircraft_click(clicked, commercial_aircraft)
        if not selected_aircraft and not selected_vessel and curve_number == vessel_curve:
            selected_vessel = resolve_vessel_click(clicked, commercial_vessels)
        if selected_aircraft:
            selected = selected_aircraft["icao24"]
            click_signature = f"{country}:{selected}"
            if st.session_state.get("handled_transport_click") != click_signature:
                st.session_state.handled_transport_click = click_signature
                st.session_state.selected_flight_icao = selected
                st.session_state.selected_vessel_mmsi = None
                st.session_state.scroll_to_flight_details = True
                # Render the selected state in a clean second cycle. This is
                # necessary for the detail anchor and expanded card to exist
                # before browser scrolling runs.
                st.rerun()
        elif selected_vessel:
            selected = str(selected_vessel["mmsi"])
            click_signature = f"{country}:vessel:{selected}"
            if st.session_state.get("handled_transport_click") != click_signature:
                st.session_state.handled_transport_click = click_signature
                st.session_state.selected_vessel_mmsi = selected
                st.session_state.selected_flight_icao = None
                st.session_state.scroll_to_vessel_details = True
                st.rerun()

    st.markdown('<div id="active-flight-details"></div>', unsafe_allow_html=True)
    st.markdown("### Active flight details")
    if not commercial_aircraft:
        st.info("No fresh likely-commercial aircraft positions are stored for this monitoring area.")
    else:
        selected_icao = st.session_state.get("selected_flight_icao")
        selected_flight = next(
            (item for item in commercial_aircraft if item.get("icao24") == selected_icao), None
        )
        # Keep the selected aircraft even when it lies outside the first 50
        # map points. Previously that mismatch caused a scroll with no card.
        remaining_aircraft = [
            item for item in commercial_aircraft
            if item.get("icao24") != selected_icao
        ][:49 if selected_flight else 50]
        ordered_aircraft = ([selected_flight] if selected_flight else []) + remaining_aircraft
        for item in ordered_aircraft:
            route, estimate = item["route"], item["estimate"]
            origin_code = route.get("origin_iata") or route.get("origin_icao")
            destination_code = route.get("destination_iata") or route.get("destination_icao")
            origin = f'{route.get("origin_name")} ({origin_code})' if route.get("origin_name") and origin_code else route.get("origin_name") or origin_code
            destination = f'{route.get("destination_name")} ({destination_code})' if route.get("destination_name") and destination_code else route.get("destination_name") or destination_code
            eta = estimate.get("eta")
            title_route = f' · {origin_code} → {destination_code}' if origin_code and destination_code else ""
            is_selected = item.get("icao24") == selected_icao
            label = f'{item.get("callsign") or item["icao24"]}{title_route}'

            def render_flight_detail():
                st.markdown(f'**Country relationship:** {item["country_relationship"]}')
                st.success(f'Likely dedicated cargo flight · {item["cargo"]["confidence"]} classification confidence')
                st.caption(item["cargo"]["basis"])
                available = [
                    ("Flight identifier", item.get("callsign") or item["icao24"]),
                    ("Aircraft ICAO24", item["icao24"].upper()),
                    ("Current position", f'{float(item["latitude"]):.4f}, {float(item["longitude"]):.4f}'),
                ]
                if item.get("altitude_m") is not None:
                    available.append(("Current altitude", f'{round(float(item["altitude_m"])):,} m'))
                if item.get("velocity_knots") is not None:
                    available.append(("Ground speed", f'{round(float(item["velocity_knots"]))} kt'))
                if item.get("track_degrees") is not None:
                    available.append(("Track", f'{round(float(item["track_degrees"]))}°'))
                if item.get("vertical_rate") is not None:
                    movement = "climbing" if float(item["vertical_rate"]) > 0 else "descending" if float(item["vertical_rate"]) < 0 else "level"
                    available.append(("Vertical movement", f'{movement} · {float(item["vertical_rate"]):+.1f} m/s'))
                if route.get("airline"): available.append(("Airline", route["airline"]))
                if origin: available.append(("Departure airport", origin))
                if destination: available.append(("Destination airport", destination))
                if estimate.get("duration_minutes") is not None:
                    available.append(("Approx. full duration", format_duration(estimate["duration_minutes"])))
                if eta: available.append(("Estimated arrival", eta.astimezone(timezone.utc).strftime("%d %b %Y · %H:%M UTC")))
                if estimate.get("route_km") is not None: available.append(("Route distance", f'{estimate["route_km"]:,} km'))
                if estimate.get("remaining_km") is not None: available.append(("Remaining distance", f'{estimate["remaining_km"]:,} km'))
                if available:
                    columns = st.columns(min(4, len(available)))
                    for index, (label, value) in enumerate(available):
                        columns[index % len(columns)].markdown(f"**{label}**  \n{value}")
                st.caption(f'Live position in {country} monitoring area · Position age: {round(age_minutes(item))} min'
                           + (" · Route matched by ADSBDB" if route.get("status") == "matched" else ""))
                st.markdown("**Transit/refuelling stops:** Not shown unless explicitly supplied by a route source. The current open route record contains only origin and destination; a geometric path must not be interpreted as a filed flight plan.")
                st.markdown("**Goods carried:** Cargo manifest unavailable from public ADS-B. The system can identify a likely dedicated freighter, but cannot determine whether it carries pharmaceuticals, machinery, perishables or another commodity.")
                if eta or estimate.get("duration_minutes") is not None:
                    st.warning("Duration and arrival are mathematical estimates from route geometry and live speed, not airline schedule times.")

            if is_selected:
                st.markdown(
                    f'<div style="border:2px solid #2567df;border-radius:10px;padding:12px 14px;'
                    f'background:#f3f7ff;margin-bottom:8px"><b>SELECTED FLIGHT · {label}</b></div>',
                    unsafe_allow_html=True,
                )
                render_flight_detail()
                st.divider()
            else:
                with st.expander(label):
                    render_flight_detail()
        if st.session_state.pop("scroll_to_flight_details", False):
            components.html("""
              <script>
                let attempts = 0;
                const scrollToDetails = () => {
                  const doc = window.parent.document;
                  const target = doc.getElementById('active-flight-details');
                  if (target) {
                    target.scrollIntoView({behavior: 'smooth', block: 'start'});
                    return;
                  }
                  attempts += 1;
                  if (attempts < 30) window.setTimeout(scrollToDetails, 100);
                };
                window.setTimeout(scrollToDetails, 150);
              </script>
            """, height=1)

    st.markdown('<div id="active-vessel-details"></div>', unsafe_allow_html=True)
    st.markdown("### Active vessel details")
    # Render the scroll component at the target rather than after the long
    # expander list. This avoids the component iframe itself becoming the
    # browser's final scroll position on large vessel result sets.
    if st.session_state.pop("scroll_to_vessel_details", False):
        components.html("""
          <script>
            let attempts = 0;
            const scrollToVessel = () => {
              const doc = window.parent.document;
              const target = doc.getElementById('active-vessel-details');
              if (target) {
                target.scrollIntoView({behavior: 'auto', block: 'start'});
                return;
              }
              attempts += 1;
              if (attempts < 30) window.setTimeout(scrollToVessel, 100);
            };
            window.setTimeout(scrollToVessel, 100);
          </script>
        """, height=1)
    if not commercial_vessels:
        st.info("No fresh likely-commercial vessel positions were returned by the configured machine-readable feeds.")
    else:
        selected_mmsi = str(st.session_state.get("selected_vessel_mmsi") or "")
        selected_vessel = next(
            (item for item in commercial_vessels if str(item.get("mmsi") or "") == selected_mmsi), None
        )
        remaining_vessels = [
            item for item in commercial_vessels if str(item.get("mmsi") or "") != selected_mmsi
        ][:49 if selected_vessel else 50]
        ordered_vessels = ([selected_vessel] if selected_vessel else []) + remaining_vessels
        for item in ordered_vessels:
            is_selected = str(item.get("mmsi") or "") == selected_mmsi
            vessel_label = item.get("ship_name") or f'MMSI {item["mmsi"]}'

            def render_vessel_detail():
                st.markdown(f"**Country relationship:** Transiting or operating in {country}'s coastal monitoring area")
                details = [("MMSI", str(item["mmsi"])),
                           ("Current position", f'{float(item["latitude"]):.4f}, {float(item["longitude"]):.4f}')]
                if item.get("vessel_type"): details.append(("Vessel type", item["vessel_type"]))
                if item.get("imo"): details.append(("IMO number", item["imo"]))
                if item.get("call_sign"): details.append(("Call sign", item["call_sign"]))
                if item.get("last_port"): details.append(("Last reported port", item["last_port"]))
                if item.get("destination"): details.append(("AIS-declared destination", item["destination"]))
                if item.get("eta"): details.append(("AIS-declared ETA", item["eta"]))
                if item.get("draught_m") is not None: details.append(("Reported draught", f'{float(item["draught_m"]):.1f} m'))
                if item.get("speed_knots") is not None: details.append(("Current speed", f'{float(item["speed_knots"]):.1f} kt'))
                if item.get("course") is not None: details.append(("Current course", f'{round(float(item["course"]))}°'))
                details.append(("Position age", f'{round(age_minutes(item))} min'))
                columns = st.columns(min(4, len(details)))
                for index, (label, value) in enumerate(details):
                    columns[index % len(columns)].markdown(f"**{label}**  \n{value}")
                st.caption(f'{item.get("source") or "AIS"} position collected within the {country} monitoring boundary.')
                if not item.get("last_port"):
                    st.markdown("**Departure/last port:** unavailable from the current open AIS message. It is not inferred from position alone.")
                if not item.get("destination"):
                    st.markdown("**Arrival/destination:** not declared in the current AIS metadata.")
                st.markdown("**Cargo information:** the AIS vessel type can identify a cargo ship, tanker or passenger vessel, but it does not reveal the actual goods or manifest.")

            if is_selected:
                st.markdown(
                    f'<div style="border:2px solid #12a676;border-radius:10px;padding:12px 14px;'
                    f'background:#effbf7;margin-bottom:8px"><b>SELECTED VESSEL · {html.escape(vessel_label)}</b></div>',
                    unsafe_allow_html=True,
                )
                render_vessel_detail()
                st.divider()
            else:
                with st.expander(vessel_label):
                    render_vessel_detail()
    st.markdown("#### Alternative live vessel maps")
    st.caption("Use these country-centred public maps when the machine-readable AIS feed is silent. Their free views may show vessel positions and AIS-declared voyage details; advanced history can require an account or subscription.")
    tracker_columns = st.columns(2)
    for index, (label, url) in enumerate(country_maritime_tracker_urls(country).items()):
        tracker_columns[index % 2].link_button(label, url, use_container_width=True)

    live_health = provider_status()
    for provider in ("OpenSky", "Fintraffic AIS", "BarentsWatch AIS", "Global Fishing Watch"):
        state = live_health.get(provider, {})
        if not state:
            st.info(f"{provider}: not collected yet.")
        elif state.get("status") != "live":
            st.warning(f'{provider}: {state.get("status", "unavailable")} — {state.get("message") or "no positions returned"}')
        else:
            st.caption(f'{provider}: {state.get("record_count", 0)} positions received at {state.get("collected_at", "unknown time")}')
    st.caption("Cargo classification identifies likely dedicated freighter operations from operator evidence; ADS-B does not expose cargo manifests or reliably identify belly freight on passenger aircraft. Positions older than 30 minutes are excluded.")


if nav == "Live transport monitor":
    show_transport_monitor()
    st.stop()


def show_disaster_monitor():
    st.markdown('<div class="panel"><h3 style="margin:0">GDACS disaster monitor</h3>'
                '<p class="subtitle">Currently active earthquakes, tropical cyclones, floods, volcanoes, droughts and wildfires. '
                'Alert colours are the official GDACS green, orange and red grades.</p></div>',
                unsafe_allow_html=True)

    status = provider_status().get("GDACS", {})
    try:
        last_collection = datetime.fromisoformat(str(status.get("collected_at") or "")).astimezone(timezone.utc)
        collection_age = (datetime.now(timezone.utc) - last_collection).total_seconds() / 60
    except (TypeError, ValueError):
        collection_age = float("inf")
    now_timestamp = datetime.now(timezone.utc).timestamp()
    auto_refresh = collection_age > 15 and now_timestamp - st.session_state.disaster_auto_attempt >= 300
    manual_refresh = st.button("Refresh active disasters", type="primary", use_container_width=True)
    if auto_refresh or manual_refresh:
        st.session_state.disaster_auto_attempt = now_timestamp
        with st.spinner("Collecting current GDACS events by disaster type…"):
            refreshed = collect_gdacs()
        status = provider_status().get("GDACS", {})
        if not refreshed:
            st.warning("GDACS returned no active events or was temporarily unavailable. The last known feed snapshot is retained below.")

    hazards = current_gdacs_signals(600)
    detailed = [{**signal, "disaster": disaster_details(signal)} for signal in hazards]
    available_types = [code for code in HAZARD_LABELS if any(item["disaster"]["event_type"] == code for item in detailed)]
    selected_types = st.multiselect(
        "Disaster types", available_types, default=available_types,
        format_func=lambda code: HAZARD_LABELS.get(code, code),
    )
    alert_filter = st.multiselect("GDACS alert grades", ["red", "orange", "green"],
                                  default=["red", "orange", "green"],
                                  format_func=str.title)
    visible = [item for item in detailed if item["disaster"]["event_type"] in selected_types
               and item["disaster"]["alert"] in alert_filter]

    d1, d2, d3, d4 = st.columns(4)
    d1.metric("Active events", len(visible))
    d2.metric("Red alerts", sum(item["disaster"]["alert"] == "red" for item in visible))
    d3.metric("Orange alerts", sum(item["disaster"]["alert"] == "orange" for item in visible))
    d4.metric("Green alerts", sum(item["disaster"]["alert"] == "green" for item in visible))

    if status.get("status") == "live":
        freshness = status.get("collected_at") or "unknown time"
        partial = f' · {status.get("message")}' if status.get("message") else ""
        st.caption(f'GDACS feed snapshot: {freshness} · {status.get("record_count", 0)} active events{partial}')
    else:
        st.warning(f'GDACS: {status.get("status", "not collected")} — {status.get("message") or "no provider response"}')

    fig = go.Figure()
    if visible:
        fig.add_trace(go.Scattergeo(
            lat=[item["latitude"] for item in visible], lon=[item["longitude"] for item in visible],
            text=[f'<b>{item["title"]}</b><br>{item["disaster"]["event_label"]} · '
                  f'{item["disaster"]["alert"].title()} alert<br>{item["disaster"]["country"] or "Location unavailable"}'
                  for item in visible],
            customdata=[item["id"] for item in visible], mode="markers", name="GDACS events",
            hovertemplate="%{text}<br>Click for event details<extra></extra>",
            marker=dict(size=[13 if item["disaster"]["alert"] == "red" else 10 if item["disaster"]["alert"] == "orange" else 8 for item in visible],
                        color=[item["disaster"]["colour"] for item in visible],
                        symbol=[item["disaster"]["symbol"] for item in visible],
                        line=dict(width=1, color="white")),
        ))
    fig.update_geos(showcountries=True, countrycolor="#91aab2", showcoastlines=True,
        coastlinecolor="#89a6b2", showland=True, landcolor="#e6eeee", showocean=True,
        oceancolor="#d8edf3", projection_type="natural earth")
    fig.update_layout(height=650, margin=dict(l=0, r=0, t=15, b=0), paper_bgcolor="white",
                      geo_bgcolor="#d8edf3")
    clicks = plotly_events(fig, click_event=True, hover_event=False, select_event=False,
                           override_height=650, key="disaster-map")
    if clicks and visible:
        point = clicks[0].get("pointNumber", clicks[0].get("pointIndex"))
        if isinstance(point, int) and 0 <= point < len(visible):
            st.session_state.selected_disaster_id = visible[point]["id"]

    if not visible:
        st.info("No active GDACS events match the selected disaster and alert filters.")
        return

    option_ids = [item["id"] for item in visible]
    label_by_id = {item["id"]: f'{item["disaster"]["alert"].upper()} · {item["disaster"]["event_label"]} · {item["title"]}' for item in visible}
    selected_id = st.session_state.selected_disaster_id
    selected_index = option_ids.index(selected_id) if selected_id in option_ids else 0
    selected_id = st.selectbox("Open disaster details", option_ids, index=selected_index,
                               format_func=lambda event_id: label_by_id[event_id])
    st.session_state.selected_disaster_id = selected_id
    selected = next(item for item in visible if item["id"] == selected_id)
    info = selected["disaster"]
    colour = info["colour"]
    st.markdown(f'<div class="panel" style="border-left:5px solid {colour}"><div class="eyebrow">ACTIVE GDACS EVENT</div>'
                f'<h3 style="margin:.2rem 0">{html.escape(selected["title"])}</h3>'
                f'<span class="pill" style="background:{colour}18;color:{colour}">{info["alert"].upper()} ALERT</span> '
                f'<span class="pill">{html.escape(info["event_label"])}</span></div>', unsafe_allow_html=True)
    st.markdown("**GDACS alert score**")
    st.progress(min(1.0, info["alert_score"] / 3.0), text=f'{info["alert_score"]:.1f}/3.0 · {info["alert"].title()}')
    st.caption("This 0–3 gauge rescales the official GDACS green/orange/red grade; it is not a probability of disaster occurrence.")
    metadata = [
        ("Country", info["country"]), ("Affected countries", ", ".join(info["affected_countries"])),
        ("Event ID", info["event_id"]), ("Episode ID", info["episode_id"]),
        ("Started", info["from_date"]), ("Expected/recorded end", info["to_date"]),
        ("Last GDACS modification", info["modified"]), ("Severity detail", info["severity_text"]),
        ("Earthquake magnitude", info["magnitude"]),
        ("Coordinates", f'{float(selected["latitude"]):.4f}, {float(selected["longitude"]):.4f}'),
    ]
    metadata = [(label, value) for label, value in metadata if value not in (None, "", [])]
    columns = st.columns(min(4, max(1, len(metadata))))
    for index, (label, value) in enumerate(metadata):
        columns[index % len(columns)].markdown(f"**{label}**  \n{value}")
    st.markdown(f'**Official summary:** {selected.get("summary") or "No summary supplied."}')
    links = st.columns(2)
    if info.get("report_url"):
        links[0].link_button("Open official GDACS report ↗", info["report_url"], use_container_width=True)
    if info.get("geometry_url"):
        links[1].link_button("Open GDACS footprint data ↗", info["geometry_url"], use_container_width=True)
    st.caption("An event remains active only while it continues to appear in recent successful GDACS feed snapshots. Older stored events are retained in SQLite but excluded from this live view.")


if nav == "Disaster monitor":
    show_disaster_monitor()
    st.stop()

@st.dialog("Country supply-chain intelligence", width="large")
def show_country_intelligence(country):
    refresh_failed = False
    if not country_cache_fresh(country, max_age_minutes=10):
        with st.spinner(f"Loading and corroborating current intelligence for {country}…"):
            refreshed = collect_country(country, enrich=True)
        refreshed_count = sum(len(items) for items in refreshed.values())
        if refreshed_count:
            # Collection occurs after the map-wide scores were calculated at
            # the beginning of this Streamlit run. Start one clean cycle and
            # reopen the dialog so every surface uses the same snapshot.
            st.session_state.reopen_country_dialog = country
            st.rerun()
        # A zero-result refresh is deliberately not considered cache-fresh.
        # Do not rerun here: doing so would immediately collect zero rows again
        # and create an endless Streamlit rerun loop.
        refresh_failed = True
    current_rows = sum((country_articles(country, category, 5) for category in CATEGORIES), [])
    # Revalidate cached rows at display time so improved country-assignment
    # rules take effect immediately, before the next network refresh.
    current_rows = [row for row in current_rows if country_supply_chain_relevance(row, country)["relevant"]]
    profile = (st.session_state.get("active_profile") or "Supply Chain Manager").strip()
    preferences = profile_feedback(profile)
    # Preferences collected in the separate general-news panel choose among a
    # recent operational candidate pool. The brief itself remains a clean
    # decision surface with no recommendation controls or recommendation score.
    current = selected_country_updates(current_rows, country, preferences)
    scoring_events = latest_country_scoring_events(current_rows, country, 10)
    assessment = assess_profile_country(country, scoring_events)
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
    if assessment.get("components"):
        component_labels = {
            "credible_event": "Strongest credible event",
            "company_exposure": "Company exposure",
            "likelihood_impact": "Likelihood × impact",
            "historical_anomaly": "Historical anomaly",
        }
        breakdown = " · ".join(
            f'{component_labels.get(name, name)} {value}/100 ({round(assessment["component_weights"][name] * 100)}%)'
            for name, value in assessment["components"].items()
        )
        st.markdown(
            f'<div class="brief"><b>Hybrid assessment basis</b><p>{assessment["model"]}: {breakdown}. '
            f'Estimated event likelihood: {assessment.get("likelihood_index", 0)}/100. '
            f'Scoring window: {assessment.get("scoring_window_size", 0)} distinct events; '
            f'{assessment.get("credible_event_count", 0)} credible, with a bounded concurrent-event uplift of +{assessment.get("concurrency_bonus", 0)}. '
            f'The confidence percentage describes evidence quality; it is not the operational risk score.</p></div>',
            unsafe_allow_html=True,
        )
    current_tab, historical_tab, forecast_tab = st.tabs(["Current trend", "Historical trend", "Forecast"])
    historical_keys = [headline_tokens(row.get("headline", "")) for row in history]
    with current_tab:
        if refresh_failed:
            st.warning("Live sources returned no new country articles. Stored reporting is shown where still current; try updating again later.")
        st.caption(f'Up to five latest qualifying country-specific updates from the 30-day reporting window are displayed newest first. The current country score separately uses {assessment.get("scoring_window_size", len(scoring_events))} distinct operational events from the latest seven days with 10% exponential recency decay.')
        if not current:
            st.info("No current supply-chain reporting was returned for this country.")
        for row in current:
            tokens = headline_tokens(row.get("headline", ""))
            matches = sum(len(tokens & old) >= 3 for old in historical_keys)
            render_article(row, priority_label=True, historical_matches=matches, show_action=True)
        if st.button("Update current intelligence", type="primary", use_container_width=True, key=f"dialog-refresh-{country}"):
            with st.spinner("Running expanded source and corroboration checks…"):
                refreshed = collect_country(country, enrich=True)
            if sum(len(items) for items in refreshed.values()):
                st.session_state.reopen_country_dialog = country
                st.rerun()
            st.warning("No new articles were returned. The current stored assessment has been preserved.")
    with historical_tab:
        st.caption("The ten latest distinct supply-chain stories retained from previous collection windows for comparison and suggested actions.")
        if not history:
            st.info("Historical intelligence will build automatically as this country is refreshed over time.")
        for row in history:
            row["summary"] = row.get("summary") or f'{row.get("publisher") or "A reporting source"} reported: {row["headline"]}.'
            render_article(row, show_action=False)
    with forecast_tab:
        forecast = forecast_country(country, scoring_events, assessment)
        st.caption(
            "Short-horizon projection of the dashboard’s operational-risk indicator from fresh country news. "
            "It is a decision-support scenario, not a claim that a particular event will occur."
        )
        if not forecast["available"]:
            st.info(forecast["reason"])
            st.markdown(
                "**What is needed:** at least one fresh qualifying operational event; multiple dated and "
                "corroborated events produce a more defensible direction and uncertainty range."
            )
        else:
            direction_colour = "#a3212b" if forecast["direction"] == "Escalating" else "#177554" if forecast["direction"] == "Easing" else "#4c607a"
            st.markdown(
                f'<div class="brief"><b style="color:{direction_colour}">{forecast["direction"].upper()}</b>'
                f'<p>{forecast["method"]} estimates a change of {forecast["slope"]:+.2f} risk-score points per day. '
                f'Forecast confidence: {forecast["confidence"]}% · evidence: {forecast["evidence_count"]} fresh events, '
                f'{forecast["corroborated_count"]} corroborated or primary-source supported.</p></div>',
                unsafe_allow_html=True,
            )
            columns = st.columns(len(forecast["projections"]))
            for column, projection in zip(columns, forecast["projections"]):
                colour = RISK_COLORS[projection["level"]]
                column.markdown(
                    f'<div class="metric"><span>NEXT {projection["days"]} DAYS</span>'
                    f'<b style="color:{colour}">{projection["score"]}/100</b>'
                    f'<small>{projection["level"]} · indicative range {projection["low"]}–{projection["high"]}</small></div>',
                    unsafe_allow_html=True,
                )
            if forecast.get("scenarios"):
                st.markdown("#### Possible operational outcomes")
                st.caption("These are evidence-derived scenarios to monitor, not predicted facts.")
                for scenario in forecast["scenarios"]:
                    st.markdown(
                        f'- {scenario["outcome"].capitalize()} '
                        f'— supported by {scenario["supporting_events"]} recent event'
                        f'{"s" if scenario["supporting_events"] != 1 else ""}.'
                    )
            st.markdown("#### Evidence driving the projection")
            for driver in forecast["drivers"]:
                driver_time = format_article_time(driver.get("published_at") or driver.get("collected_at"))
                st.markdown(
                    f'<div class="event">{driver_time}<b>{html.escape(driver.get("headline") or "Operational update")}</b>'
                    f'<p>Article impact {driver["risk"]["score"]}/100 · {driver["risk"]["level"]} · '
                    f'{driver["risk"].get("source_count", 0)} independent source families</p></div>',
                    unsafe_allow_html=True,
                )
            st.markdown(f'**Suggested preparation:** {forecast["action"]}')
            st.caption(
                f'Model residual error: {forecast["rmse"]:.2f} score points. The displayed range is an '
                'uncertainty indicator and is not a calibrated probability interval.'
            )


# Feedback buttons inside a dialog cause Streamlit to rerun the script. Reopen
# only the same country dialog for that rerun, preserving the user's workflow
# without making a manually closed dialog reappear indefinitely.
reopen_country = st.session_state.pop("reopen_country_dialog", None)

# All entry points queue a single country-dialog request. Rendering it once at
# the end of the page prevents a retained Plotly click and a dropdown rerun from
# attempting to open two Streamlit dialogs during the same script execution.
requested_country = st.session_state.pop("open_country_brief", None)
dialog_country_to_open = reopen_country if reopen_country in COUNTRY_NAMES else requested_country
if dialog_country_to_open not in COUNTRY_NAMES:
    dialog_country_to_open = None


def render_general_news_panel():
    st.markdown('<div class="panel"><h3 style="margin:0">General news interests</h3><p class="subtitle">Each story may have several automatically assigned categories. Your ratings influence the operational stories selected in country briefs.</p></div>', unsafe_allow_html=True)
    default_country = st.session_state.country if st.session_state.country in COUNTRY_NAMES else "United States"
    news_country = st.selectbox("General news country", COUNTRY_NAMES,
                                index=COUNTRY_NAMES.index(default_country), key="general_news_country")
    refresh_news = st.button("Refresh general news", use_container_width=True, key="refresh-general-news")
    if refresh_news:
        cached_general_news.clear()
    try:
        with st.spinner(f"Loading general news for {news_country}…"):
            news_rows = cached_general_news(news_country)
    except Exception as exc:
        st.warning(f"General news is temporarily unavailable: {exc}")
        news_rows = []
    profile = (st.session_state.get("active_profile") or "Supply Chain Manager").strip()
    st.caption(f"Latest {len(news_rows)} of 10 · ratings saved to {profile}")
    if not news_rows:
        st.info("No recent general-news results were returned. Try Refresh general news shortly.")
        return
    with st.container(height=760, border=True):
        for position, article in enumerate(news_rows, 1):
            key = article_key(article)
            existing = feedback_for_articles(profile, [key]).get(key)
            published = article_datetime(article)
            date_text = "Date unavailable" if published.year == 1 else published.strftime("%d %b %Y · %H:%M UTC")
            try:
                sources = json.loads(article.get("sources_json") or "[]")
            except (TypeError, json.JSONDecodeError):
                sources = []
            if not sources:
                sources = [{"publisher": article.get("publisher") or "Open source",
                            "url": article.get("url") or "#"}]
            categories = article.get("general_categories") or classify_general_news(article)["labels"]
            category_badges = " ".join(
                f'<span class="pill" style="background:#eef3f8;color:#43556b;margin-right:3px">'
                f'{html.escape(label)}</span>' for label in categories
            )
            links = " · ".join(
                f'<a href="{html.escape(source.get("url") or "#", quote=True)}" target="_blank">'
                f'{html.escape(source.get("publisher") or "Open source")}</a>' for source in sources
            )
            st.markdown(
                f'<div style="font-size:9px;color:#718096;font-weight:700">{position} · {date_text}</div>'
                f'<div style="font-size:12px;font-weight:800;line-height:1.35;margin:.25rem 0">'
                f'{html.escape(article.get("headline") or "Untitled report")}</div>'
                f'<div style="margin:.2rem 0 .35rem">{category_badges}</div>'
                f'<div style="font-size:9px">Sources: {links}</div>', unsafe_allow_html=True,
            )
            like, dislike = st.columns(2)
            like.button("👍 Interested", key=f"general-like-{profile}-{key}",
                        type="primary" if existing == 1 else "secondary", use_container_width=True,
                        on_click=record_preference, args=(profile, key, article, 1, "General news"))
            dislike.button("👎 Not interested", key=f"general-dislike-{profile}-{key}",
                           type="primary" if existing == -1 else "secondary", use_container_width=True,
                           on_click=record_preference, args=(profile, key, article, -1, "General news"))
            st.divider()


map_col, news_col = st.columns([3, 1], gap="large")
with map_col:
    st.markdown('''<div class="panel"><h3 style="margin:0">Global supply-chain intelligence map</h3><p class="subtitle">Hover to identify a country. Click anywhere inside a country to open its current and historical supply-chain intelligence. Colours show the latest recalculated operational-risk band.</p><div class="legend"><span><i style="background:#27a66a"></i>Low</span><span><i style="background:#e5b62f"></i>Moderate</span><span><i style="background:#d9363e"></i>Critical</span></div></div>''', unsafe_allow_html=True)
    selector_col, open_col = st.columns([5, 1])
    with selector_col:
        selected_country = st.selectbox("Search for a country", COUNTRY_NAMES, index=None,
                                        placeholder="Type a country name…", key="map_country_selector")
    with open_col:
        st.write("")
        open_selected_country = st.button("Open intelligence", type="primary", use_container_width=True,
                                          disabled=selected_country is None)
    dropdown_country_changed = selected_country is not None and selected_country != st.session_state.last_dropdown_country
    if dropdown_country_changed:
        st.session_state.last_dropdown_country = selected_country
    if selected_country and (open_selected_country or dropdown_country_changed):
        st.session_state.country = selected_country
        st.session_state.open_country_brief = selected_country
        st.rerun()

    map_status = {name: country_risks[name]["level"] for _, name in COUNTRIES}
    map_values = {"Low": 0, "Moderate": 1, "Critical": 2}
    fig = go.Figure(go.Choropleth(
        locations=[code for code, _ in COUNTRIES], locationmode="ISO-3",
        z=[map_values[map_status[name]] for _, name in COUNTRIES],
        customdata=[[name, map_status[name], country_risks[name]["score"], country_risks[name]["confidence"],
                     country_risks[name].get("evidence_count", 0)] for _, name in COUNTRIES], zmin=0, zmax=2,
        colorscale=[[0, "#27a66a"], [0.2499, "#27a66a"], [0.25, "#e5b62f"],
                    [0.7499, "#e5b62f"], [0.75, "#d9363e"], [1, "#d9363e"]],
        showscale=False, marker_line_color="#8faab4", marker_line_width=0.6,
        hovertemplate="<b>%{customdata[0]}</b><br>%{customdata[1]} · score %{customdata[2]}/100<br>Confidence %{customdata[3]}% · %{customdata[4]} fresh evidence items<br>Click for intelligence<extra></extra>",
    ))
    fig.update_geos(showcountries=True, countrycolor="#91aab2", showcoastlines=True,
        coastlinecolor="#89a6b2", showland=True, landcolor="#dce8e8", showocean=True, oceancolor="#d8edf3",
        projection_type="natural earth")
    fig.update_layout(height=760, margin=dict(l=0,r=0,t=0,b=0), paper_bgcolor="white", geo_bgcolor="#d8edf3", clickmode="event+select")
    clicks = plotly_events(
        fig, click_event=True, hover_event=False, select_event=False,
        override_height=760, key=f"country-map-{st.session_state.country_map_epoch}",
    )
    if clicks:
        try:
            clicked_country = fig.data[0].customdata[clicks[0].get("pointNumber", 0)][0]
            st.session_state.country = clicked_country
            st.session_state.open_country_brief = clicked_country
            # A new component key consumes the click. Without this, the
            # streamlit-plotly-events component may replay its previous value
            # when controls inside the dialog trigger a rerun.
            st.session_state.country_map_epoch += 1
            st.rerun()
        except (IndexError, TypeError):
            pass
with news_col:
    render_general_news_panel()

if dialog_country_to_open:
    show_country_intelligence(dialog_country_to_open)

st.caption("OSINT Early Warning Dashboard 2.0 · Supply-chain decision support · Docker deployment · SQLite persistence · Verify automated assessments against linked primary sources.")
