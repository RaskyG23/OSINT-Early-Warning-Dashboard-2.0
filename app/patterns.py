import hashlib
import json
import re
import statistics
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

from app.database import (
    pattern_baseline,
    pattern_candidates,
    record_article_history,
    record_pattern_window,
    upsert_story_pattern,
)

STOPWORDS = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "from", "with", "as", "at", "by", "after", "over", "says", "new", "latest"}
ROUTES = {
    "Suez Canal": ("suez",), "Panama Canal": ("panama canal",), "Red Sea": ("red sea",),
    "Strait of Hormuz": ("hormuz",), "Strait of Malacca": ("malacca",),
    "Black Sea": ("black sea",), "English Channel": ("english channel",),
    "Bosporus": ("bosporus", "bosphorus"), "Baltic Sea": ("baltic sea",),
}
MODE_TERMS = {
    "Maritime": ("port", "shipping", "ship", "vessel", "canal", "strait", "tanker", "container", "harbour", "harbor"),
    "Aviation": ("airport", "airspace", "airline", "flight", "air cargo", "airfreight", "runway"),
    "Road": ("road", "motorway", "highway", "truck", "border crossing"),
    "Rail": ("rail", "railway", "train", "freight line"),
    "Energy": ("pipeline", "refinery", "oil field", "gas terminal", "power grid"),
}
OPERATIONAL_TERMS = {
    "closure", "closed", "blocked", "strike", "delay", "disruption", "shortage", "sanction",
    "attack", "war", "conflict", "flood", "earthquake", "wildfire", "storm", "outage", "protest",
    "evacuation", "embargo", "congestion", "diversion",
}
EVENT_ACTORS = {"houthi", "houthis", "iran", "israel", "russia", "ukraine", "hezbollah", "hamas"}
EVENT_ACTIONS = {"attack", "attacks", "attacked", "strike", "strikes", "struck", "closure", "closed", "blockade", "blocked", "collision", "fire", "explosion", "seized"}
EVENT_TARGETS = {"ship", "shipping", "vessel", "tanker", "port", "airport", "airspace", "flight", "cargo", "freight"}
EVENT_ROUTES = {"mandeb", "hormuz", "suez", "red", "gulf", "aden", "black", "malacca", "panama"}
EVENT_EQUIVALENTS = {
    "attacks": "attack", "attacked": "attack", "strikes": "strike", "struck": "strike",
    "shipping": "ship", "vessels": "vessel", "flights": "flight",
}


def story_key(headline):
    clean = re.sub(r"\s+-\s+[^-]+$", "", (headline or "").lower())
    return " ".join(word for word in re.findall(r"[a-z0-9]+", clean) if word not in STOPWORDS)


def same_story(left, right):
    left_tokens, right_tokens = set(left.split()), set(right.split())
    union = left_tokens | right_tokens
    overlap = len(left_tokens & right_tokens) / len(union) if union else 0
    return overlap >= 0.48 or SequenceMatcher(None, left, right).ratio() >= 0.72


def event_match(left_headline, right_headline):
    """Match differently worded coverage using core event anchors."""
    normalize = lambda words: {EVENT_EQUIVALENTS.get(word, word) for word in words}
    left = normalize(story_key(left_headline).split())
    right = normalize(story_key(right_headline).split())
    if same_story(" ".join(left), " ".join(right)):
        return True
    actor = left & right & EVENT_ACTORS
    action = left & right & EVENT_ACTIONS
    target = left & right & EVENT_TARGETS
    route = left & right & EVENT_ROUTES
    return bool(action and target and (actor or route) and len(left & right) >= 3)


def fact_variance(left_headline, right_headline):
    left_numbers = set(re.findall(r"\b\d+\b", left_headline or ""))
    right_numbers = set(re.findall(r"\b\d+\b", right_headline or ""))
    if left_numbers and right_numbers and left_numbers != right_numbers:
        return f'Numeric details differ ({", ".join(sorted(left_numbers))} vs {", ".join(sorted(right_numbers))})'
    return ""


def synthesize_event_headline(headlines, country):
    """Create a specific, neutral event headline without copying an outlet's wording."""
    clean = [re.sub(r"\s+-\s+[^-]+$", "", value or "").strip() for value in headlines if value]
    if not clean:
        return f"Supply-chain development affecting {country}"
    token_sets = [set(story_key(value).split()) for value in clean]
    all_tokens = set().union(*token_sets)
    token_frequency = {
        token: sum(token in tokens for tokens in token_sets)
        for token in all_tokens
    }
    # The best-supported detailed headline is the safe extractive fallback for
    # events outside the rule vocabulary. This prevents unrelated stories from
    # collapsing into the same generic label.
    representative = max(
        clean,
        key=lambda value: (
            sum(3 if token_frequency.get(token, 0) > 1 else 1
                for token in set(story_key(value).split())),
            len(value),
        ),
    )
    representative = re.sub(
        r"^(?:live\s+updates?|latest\s+(?:news|updates?)|breaking(?:\s+news)?)\s*[:\-–—]\s*",
        "", representative, flags=re.IGNORECASE,
    ).strip(" .:;-–—")
    actors = sorted(all_tokens & EVENT_ACTORS)
    routes = []
    lowered = " ".join(clean).casefold()
    route_names = {
        "bab el-mandeb": ("bab el-mandeb", "bab al-mandab", "bab el mandeb"),
        "Strait of Hormuz": ("hormuz",), "Red Sea": ("red sea",), "Suez Canal": ("suez",),
        "Gulf of Aden": ("gulf of aden",), "Black Sea": ("black sea",),
    }
    for label, markers in route_names.items():
        if any(marker in lowered for marker in markers):
            routes.append(label)
    actor_labels = {
        "houthis": "Houthi forces", "houthi": "Houthi forces", "iran": "Iran",
        "israel": "Israel", "russia": "Russia", "ukraine": "Ukraine",
        "hezbollah": "Hezbollah", "hamas": "Hamas",
    }
    actor = actor_labels.get(actors[0], actors[0].title()) if actors else ""

    # Prefer the most operationally precise asset phrase present in the coverage.
    asset_markers = (
        ("container terminal operations", ("container terminal", "container operations")),
        ("commercial vessel", ("commercial vessel", "merchant vessel", "cargo ship")),
        ("oil tanker", ("oil tanker", "crude tanker", "fuel tanker")),
        ("container ship", ("container ship",)),
        ("airport cargo operations", ("airport cargo", "air cargo", "airport freight")),
        ("port operations", ("port operations", "port closure", "port closed", "port")),
        ("commercial shipping", ("shipping", "ship", "vessel")),
        ("airspace and flight operations", ("airspace", "flight", "airline")),
        ("freight operations", ("cargo", "freight")),
    )
    asset = next((label for label, markers in asset_markers if any(marker in lowered for marker in markers)),
                 "supply-chain operations")
    location = f" in {routes[0]}" if routes else f" affecting {country}"

    if any(term in lowered for term in ("missile attack", "missile strike")):
        event = f"{actor + ' ' if actor else ''}missile attack targets {asset}"
    elif any(term in lowered for term in ("drone attack", "drone strike")):
        event = f"{actor + ' ' if actor else ''}drone attack targets {asset}"
    elif any(term in lowered for term in ("walkout", "industrial action", "workers strike", "worker strike")):
        event = f"Industrial action disrupts {asset}"
    elif any(term in lowered for term in ("attack", "attacked", "strike", "struck")):
        event = f"{actor + ' ' if actor else ''}attack targets {asset}"
    elif any(term in lowered for term in ("closure", "closed", "shutdown", "shut down")):
        event = f"Closure of {asset} disrupts transport activity"
    elif any(term in lowered for term in ("blockade", "blocked")):
        event = f"Blockage disrupts {asset}"
    elif "collision" in lowered:
        event = f"Collision disrupts {asset}"
    elif any(term in lowered for term in ("seized", "seizure")):
        event = f"Seizure interrupts {asset}"
    else:
        # Retain the concrete subject and action supplied by the reporting when
        # no specialised template applies. It is more informative than a
        # fabricated generic disruption statement and remains source-neutral.
        return representative[:1].upper() + representative[1:]

    consequences = []
    if any(term in lowered for term in ("killed", "fatalities", "dead", "deaths")):
        consequences.append("fatalities reported")
    if any(term in lowered for term in ("reroute", "rerouting", "divert", "diversion")):
        consequences.append("route diversions")
    if any(term in lowered for term in ("delay", "delays", "delayed", "congestion", "backlog")):
        consequences.append("cargo delays")
    if any(term in lowered for term in ("suspend", "suspended", "cancelled", "canceled", "halt", "halted")):
        consequences.append("services suspended")
    if any(term in lowered for term in ("damage", "damaged", "fire", "explosion")):
        consequences.append("physical damage reported")
    headline = event + location
    if consequences:
        headline += ", with " + " and ".join(consequences[:2])
    return headline[:1].upper() + headline[1:]


def synthesize_event_summary(headlines, country):
    """Summarise agreement and disagreement without inventing unsupported facts."""
    count = len(headlines)
    headline = synthesize_event_headline(headlines, country)
    number_sets = [set(re.findall(r"\b\d+\b", value or "")) for value in headlines]
    reported_numbers = sorted(set().union(*number_sets)) if number_sets else []
    disagreement = len({tuple(sorted(values)) for values in number_sets if values}) > 1
    summary = f"{count} independent source headline{'s' if count != 1 else ''} describe the same event: {headline.rstrip('.')} ."
    if disagreement:
        summary += f" Published numeric details differ across sources ({', '.join(reported_numbers)}), so the consolidated headline does not assert a disputed figure."
    return summary.replace(" .", ".")


def robust_z_score(value, baseline):
    """Median/MAD z-score. Returns zero until five comparable windows exist."""
    clean = [float(item) for item in baseline if item is not None]
    if len(clean) < 5:
        return 0.0
    median = statistics.median(clean)
    mad = statistics.median(abs(item - median) for item in clean)
    if mad > 0:
        return round(0.6745 * (float(value) - median) / mad, 2)
    spread = statistics.pstdev(clean)
    return round((float(value) - median) / spread, 2) if spread > 0 else 0.0


def persistence_score(first_seen, active_windows, source_count):
    try:
        first = datetime.fromisoformat(first_seen.replace("Z", "+00:00"))
        age_hours = max(0, (datetime.now(timezone.utc) - first.astimezone(timezone.utc)).total_seconds() / 3600)
    except (TypeError, ValueError):
        age_hours = 0
    return round(min(100, 20 + min(40, max(0, active_windows - 1) * 12)
                         + min(20, age_hours / 6) + min(20, max(0, source_count - 1) * 4)))


def extract_supply_chain_context(headline, summary=""):
    """Phase 2: transparent entity, route and transport-mode extraction."""
    text = f"{headline or ''} {summary or ''}"; lowered = text.casefold()
    routes = [name for name, markers in ROUTES.items() if any(marker in lowered for marker in markers)]
    modes = [mode for mode, markers in MODE_TERMS.items() if any(marker in lowered for marker in markers)]
    entities = []
    for match in re.findall(r"\b(?:[A-Z][\w’-]+(?:\s+|$)){1,4}", headline or ""):
        value = match.strip()
        if len(value) > 2 and value.casefold() not in {"the", "a", "an"} and value not in entities:
            entities.append(value)
    entities.extend(route for route in routes if route not in entities)
    relevance_hits = len(set(re.findall(r"[a-z]+", lowered)) & OPERATIONAL_TERMS)
    relevance = min(100, 15 + relevance_hits * 14 + len(modes) * 10 + len(routes) * 15)
    return {"entities": entities[:12], "routes": routes, "transport_modes": modes or ["Multimodal / indirect"],
            "operational_relevance": relevance}


def cusum_change_score(value, baseline):
    """Phase 2: one-sided robust CUSUM for sustained upward activity shifts."""
    clean = [float(item) for item in baseline if item is not None]
    if len(clean) < 5:
        return 0.0, "Baseline building"
    centre = statistics.median(clean)
    mad = statistics.median(abs(item - centre) for item in clean)
    scale = max(1.0, 1.4826 * mad)
    cumulative = 0.0
    for item in clean[-10:] + [float(value)]:
        cumulative = max(0.0, cumulative + (item - centre) / scale - 0.5)
    score = round(min(100, cumulative / 5 * 100), 1)
    status = "Structural shift" if score >= 70 else "Developing shift" if score >= 40 else "Stable range"
    return score, status


def early_warning_assessment(anomaly, persistence, change, source_count, relevance, baseline_windows):
    """Phase 3: explainable multi-factor warning score; not a probability forecast."""
    anomaly_component = min(100, max(0, anomaly) / 3 * 100)
    source_component = min(100, source_count * 20)
    score = round(0.25 * anomaly_component + 0.25 * persistence + 0.20 * change
                  + 0.15 * source_component + 0.15 * relevance)
    level = "Critical" if score >= 70 else "Elevated" if score >= 45 else "Watch" if score >= 25 else "Informational"
    confidence = round(min(95, 20 + min(25, source_count * 5) + min(20, baseline_windows * 3)
                           + min(20, persistence / 5) + (10 if relevance >= 50 else 0)))
    rationale = [
        f"{source_count} distinct reporting source{'s' if source_count != 1 else ''}",
        f"persistence {persistence:.0f}/100", f"change score {change:.0f}/100",
        f"operational relevance {relevance:.0f}/100",
    ]
    if baseline_windows < 5:
        rationale.append("volume baseline is still building")
    elif anomaly > 0:
        rationale.append(f"news-volume anomaly robust z {anomaly:+.2f}")
    return {"score": score, "level": level, "confidence": confidence, "rationale": rationale}


def _sources(value):
    try:
        return json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []


def analyze_observation(country, category, stories, candidate_count, collected_at):
    """Store a refresh window and update cross-refresh story patterns."""
    baseline = pattern_baseline(country, category)
    anomaly = robust_z_score(len(stories), baseline)
    change_score, change_status = cusum_change_score(len(stories), baseline)
    candidates = pattern_candidates(
        country, category,
        (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),
    )
    touched = []
    for story in stories[:20]:
        key = story_key(story.get("headline"))
        if not key:
            continue
        match = next((item for item in candidates if same_story(key, item["canonical_key"])), None)
        incoming_sources = _sources(story.get("sources_json"))
        if match:
            existing_sources = _sources(match.get("sources_json"))
            seen = {(item.get("publisher", "").casefold(), item.get("url", "")) for item in existing_sources}
            for source in incoming_sources:
                marker = (source.get("publisher", "").casefold(), source.get("url", ""))
                if marker not in seen:
                    existing_sources.append(source); seen.add(marker)
            active_windows = match["active_windows"] + (1 if match["last_seen"] != collected_at else 0)
            first_seen = match["first_seen"]
            pattern_id = match["pattern_id"]
            observations = match["observation_count"] + 1
            sources = existing_sources
        else:
            pattern_id = hashlib.sha1(f"{country}|{category}|{key}".encode()).hexdigest()
            first_seen = collected_at
            active_windows = 1
            observations = 1
            sources = incoming_sources
        source_count = max(1, len(sources))
        persistence = persistence_score(first_seen, active_windows, source_count)
        status = "Persistent" if active_windows >= 3 or persistence >= 70 else "Developing" if active_windows >= 2 else "Emerging"
        context = extract_supply_chain_context(story.get("headline"), story.get("summary"))
        warning = early_warning_assessment(anomaly, persistence, change_score, source_count,
                                           context["operational_relevance"], len(baseline))
        row = {
            "pattern_id": pattern_id, "country": country, "category": category,
            "canonical_key": key, "headline": story.get("headline") or "Untitled pattern",
            "first_seen": first_seen, "last_seen": collected_at,
            "observation_count": observations, "active_windows": active_windows,
            "source_count": source_count, "anomaly_score": anomaly,
            "persistence_score": persistence, "status": status,
            "entities_json": json.dumps(context["entities"], ensure_ascii=False),
            "routes_json": json.dumps(context["routes"], ensure_ascii=False),
            "transport_modes_json": json.dumps(context["transport_modes"], ensure_ascii=False),
            "change_score": change_score, "change_status": change_status,
            "early_warning_score": warning["score"], "alert_level": warning["level"],
            "alert_confidence": warning["confidence"],
            "rationale_json": json.dumps(warning["rationale"], ensure_ascii=False),
            "sources_json": json.dumps(sources, ensure_ascii=False),
        }
        upsert_story_pattern(row)
        touched.append(row)
        if not match:
            candidates.append(row)
    record_article_history(country, category, stories[:20], collected_at, story_key)
    record_pattern_window(country, category, candidate_count, len(stories), collected_at)
    return touched
