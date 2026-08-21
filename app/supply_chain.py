import json
import re
from datetime import datetime, timezone

MARITIME_TERMS = {"port", "shipping", "ship", "vessel", "maritime", "harbour", "harbor", "canal", "strait", "freight", "container", "dock", "tanker"}
AVIATION_TERMS = {"airport", "airspace", "aircraft", "aviation", "airline", "flight", "runway", "airfreight", "drone", "missile", "no-fly"}
DISRUPTION_TERMS = {"strike", "closure", "close", "closed", "blocked", "delay", "delayed", "disruption", "disrupted", "shortage", "sanction", "attack", "attacks", "war", "conflict", "flood", "earthquake", "wildfire", "storm", "cyclone", "outage", "blackout", "protest", "halt", "halted", "halting", "suspended", "damaged", "rerouted", "cancelled", "canceled"}
CRITICAL_TERMS = {"war", "attack", "attacks", "missile", "close", "closed", "closure", "blocked", "earthquake", "cyclone", "sanction", "outage", "halted", "halting", "suspended", "damaged"}
OPERATIONAL_CONSEQUENCE_TERMS = {
    "closure", "close", "closes", "closed", "blocked", "delay", "delayed", "disruption", "disrupted", "shortage",
    "suspension", "suspended", "cancelled", "canceled", "cancellation", "reroute", "rerouted",
    "rerouting", "diverted", "diversion", "congestion", "backlog", "damage", "damaged", "outage",
    "blackout", "grounded", "halted", "stopped", "shutdown", "strike", "sanction", "restricted",
    "restriction", "embargo", "seized",
}
ARMED_THREAT_TERMS = {"war", "attack", "attacks", "missile", "drone", "conflict", "hostilities", "houthi", "seized"}
NATURAL_HAZARD_TERMS = {
    "earthquake", "aftershock", "tsunami", "flood", "flooding", "wildfire", "bushfire",
    "cyclone", "hurricane", "typhoon", "storm", "volcano", "volcanic", "eruption",
    "drought", "landslide", "mudslide",
}
HAZARD_EXPOSURE_TERMS = {
    "road", "bridge", "rail", "port", "airport", "terminal", "warehouse", "factory",
    "plant", "supplier", "cargo", "logistics", "power", "electricity", "utility", "telecoms",
}
STRATEGIC_ROUTE_MARKERS = {
    "bab el-mandeb", "bab-el-mandeb", "red sea", "suez", "hormuz", "persian gulf", "gulf of aden",
    "black sea", "malacca", "panama canal", "english channel", "bosporus", "bosphorus",
}
POSITIVE_TONE_TERMS = {"reopens", "reopened", "resume", "resumes", "recovery", "restored", "agreement", "deal", "growth", "improves", "improved", "boost", "expands", "investment", "surplus", "eases"}
NEGATIVE_TONE_TERMS = DISRUPTION_TERMS | {"crisis", "risk", "threat", "fatalities", "killed", "loss", "decline", "collapse", "escalates", "impasse", "volatile", "warning"}
COUNTRY_POSITIVE_TERMS = {
    "agreement": 2, "boost": 2, "expansion": 2, "growth": 2, "improved": 2,
    "investment": 2, "recovery": 3, "reopened": 3, "restored": 3, "resumed": 2,
    "stable": 1, "surplus": 2, "upgrade": 2,
}
COUNTRY_NEGATIVE_TERMS = {
    "attack": 3, "attacks": 3, "blackout": 3, "blocked": 3, "cancelled": 2,
    "closure": 3, "collapse": 3, "conflict": 3, "crisis": 3, "damage": 2,
    "damaged": 3, "delay": 2, "delayed": 2, "disruption": 3, "drought": 2,
    "earthquake": 3, "fatalities": 4, "flood": 3, "halted": 3, "outage": 3,
    "risk": 1, "sanction": 2, "shortage": 3, "strike": 2, "threat": 2,
    "war": 4, "wildfire": 3,
}
NEGATIONS = {"no", "not", "never", "without", "avoids", "avoided", "prevented"}
SIGNAL_STOPWORDS = {
    "a", "an", "the", "and", "or", "in", "on", "at", "to", "for", "of", "from", "with", "by",
    "signal", "near", "general", "reports", "reported", "event", "alert", "country", "identified",
    "referenced", "mentions", "articles", "sources", "gdelt",
}

ASSET_TERMS = {
    "port", "harbour", "harbor", "airport", "airspace", "terminal", "runway", "canal",
    "strait", "border", "crossing", "bridge", "road", "railway", "rail", "warehouse",
    "factory", "plant", "refinery", "pipeline", "power", "grid", "customs", "depot",
}
RESOLUTION_TERMS = {"reopened", "reopens", "resumed", "resumes", "restored", "resolved", "recovery", "cleared"}
ACTIVE_STATUS_TERMS = {"ongoing", "continues", "continuing", "current", "active", "until", "indefinite", "indefinitely"}
DEVELOPING_STATUS_TERMS = {"warning", "threat", "threatens", "risk", "expected", "forecast", "possible"}

# Controlled country names used only for target identification. Short
# acronyms are matched case-sensitively so ordinary words such as "us" do not
# assign an unrelated story to the United States.
COUNTRY_TEXT_ALIASES = {
    "United Kingdom": ("United Kingdom", "Britain", "British", "England", "Scotland", "Wales", "Northern Ireland", "UK", "U.K."),
    "United States": ("United States", "United States of America", "America", "USA", "U.S.A.", "US", "U.S."),
    "United States of America": ("United States", "United States of America", "America", "USA", "U.S.A.", "US", "U.S."),
    "Netherlands": ("Netherlands", "The Netherlands", "Holland", "Dutch"),
    "United Arab Emirates": ("United Arab Emirates", "Emirati", "UAE", "U.A.E."),
    "Russia": ("Russia", "Russian Federation", "Russian"),
    "Democratic Republic of the Congo": ("Democratic Republic of the Congo", "DR Congo", "DRC", "Congolese"),
    "Congo": ("Congo", "Republic of the Congo", "Congo-Brazzaville"),
    "Côte d'Ivoire": ("Côte d'Ivoire", "Cote d'Ivoire", "Ivory Coast", "Ivorian"),
    "Czechia": ("Czechia", "Czech Republic"),
    "Türkiye": ("Türkiye", "Turkiye", "Turkey"),
    "Myanmar": ("Myanmar", "Burma"),
    "Eswatini": ("Eswatini", "Swaziland"),
    "Cabo Verde": ("Cabo Verde", "Cape Verde"),
    "Timor-Leste": ("Timor-Leste", "East Timor"),
    "North Macedonia": ("North Macedonia", "Republic of North Macedonia"),
    "Vietnam": ("Vietnam", "Viet Nam"),
    "Laos": ("Laos", "Lao PDR", "Lao People's Democratic Republic"),
    "South Korea": ("South Korea", "Republic of Korea", "ROK", "R.O.K."),
    "North Korea": ("North Korea", "DPRK", "D.P.R.K.", "Democratic People's Republic of Korea"),
    "Taiwan": ("Taiwan", "Chinese Taipei"),
    "Moldova": ("Moldova", "Republic of Moldova"),
    "Tanzania": ("Tanzania", "United Republic of Tanzania"),
    "Bolivia": ("Bolivia", "Plurinational State of Bolivia"),
    "Venezuela": ("Venezuela", "Bolivarian Republic of Venezuela"),
    "Brunei": ("Brunei", "Brunei Darussalam"),
    "Syria": ("Syria", "Syrian Arab Republic"),
    "Palestine": ("Palestine", "State of Palestine", "Palestinian territories"),
}
CASE_SENSITIVE_COUNTRY_ALIASES = {"Turkey", "Congo"}

_EMBEDDING_MODEL = None


def set_embedding_model(model):
    """Install the current corpus model for risk assessments in this process."""
    global _EMBEDDING_MODEL
    _EMBEDDING_MODEL = model


def _tokens(text):
    return set(re.findall(r"[a-z]+(?:-[a-z]+)?", (text or "").lower()))


def country_mentioned(text, country):
    """Return whether text explicitly identifies a country or safe alias."""
    if not text or not country:
        return False
    aliases = COUNTRY_TEXT_ALIASES.get(country, (country,))
    for alias in aliases:
        letters = re.sub(r"[^A-Za-z]", "", alias)
        if len(letters) <= 3:
            acronym = r"\.?".join(re.escape(letter) for letter in letters)
            if re.search(rf"(?<![A-Za-z]){acronym}\.?(?![A-Za-z])", text):
                return True
            continue
        if alias in CASE_SENSITIVE_COUNTRY_ALIASES:
            if re.search(rf"(?<![A-Za-z]){re.escape(alias)}(?![A-Za-z])", text):
                return True
            continue
        if re.search(rf"(?<![a-z]){re.escape(alias.casefold())}(?![a-z])", text.casefold()):
            return True
    return False


def tone_assessment(text):
    """Transparent lexicon tone score from -100 (negative) to +100 (positive)."""
    tokens = _tokens(text)
    positive = len(tokens & POSITIVE_TONE_TERMS)
    negative = len(tokens & NEGATIVE_TONE_TERMS)
    score = round(max(-100, min(100, (positive - negative) / max(1, positive + negative) * 100)))
    label = "Positive" if score >= 25 else "Negative" if score <= -25 else "Neutral"
    return {"score": score, "label": label, "positive_hits": positive, "negative_hits": negative}


def country_sentiment(text, country):
    """Score sentiment directed at the selected country, from -100 to +100."""
    sentences = [part.strip() for part in re.split(r"(?<=[.!?;])\s+|\s+[–—]\s+", text or "") if part.strip()]
    targeted = [sentence for sentence in sentences if country_mentioned(sentence, country)]
    analysed = targeted or sentences
    words = re.findall(r"[a-z]+(?:-[a-z]+)?", " ".join(analysed).casefold())
    raw = 0
    positive_hits, negative_hits = [], []
    for index, word in enumerate(words):
        weight = COUNTRY_POSITIVE_TERMS.get(word, 0) - COUNTRY_NEGATIVE_TERMS.get(word, 0)
        if not weight:
            continue
        if set(words[max(0, index - 3):index]) & NEGATIONS:
            weight *= -1
        raw += weight
        (positive_hits if weight > 0 else negative_hits).append(word)
    score = round(max(-100, min(100, 100 * raw / (abs(raw) + 3)))) if raw else 0
    label = "Positive" if score >= 15 else "Negative" if score <= -15 else "Neutral"
    return {"score": score, "label": label, "target": country or "Country unspecified",
            "target_explicit": bool(targeted), "positive_hits": positive_hits,
            "negative_hits": negative_hits, "method": "Country-targeted weighted lexicon"}


def country_supply_chain_relevance(article, country):
    """Score evidence that a story affects the selected country's operations."""
    headline = article.get("headline", "")
    text = f'{headline} {article.get("summary", "")}'
    lowered = text.casefold()
    tokens = _tokens(text)
    country_explicit = country_mentioned(headline, country)
    transport = bool(tokens & (MARITIME_TERMS | AVIATION_TERMS | HAZARD_EXPOSURE_TERMS | {"truck", "border", "customs"}))
    hazard = bool(tokens & NATURAL_HAZARD_TERMS)
    operational = bool(tokens & (OPERATIONAL_CONSEQUENCE_TERMS | ARMED_THREAT_TERMS | NATURAL_HAZARD_TERMS))
    primary = article.get("coverage_scope") == "Primary operational"
    local = article.get("coverage_scope") == "Local / regional"
    strategic = any(marker in lowered for marker in STRATEGIC_ROUTE_MARKERS)
    score = (45 if country_explicit else 0) + (30 if operational else 0) + (20 if transport else 0)
    score += 10 if primary else 5 if local else 0
    score += 5 if strategic else 0
    reasons = []
    if country_explicit: reasons.append("selected country or a controlled alias is explicitly identified")
    if operational: reasons.append("an operational threat or consequence is described")
    if transport: reasons.append("transport or logistics exposure is identified")
    if primary: reasons.append("reported by a primary operational source")
    # Primary/operational-source status strengthens evidence but cannot assign
    # an international story to a country that its publisher headline does not
    # identify. This prevents global LNG or nearby-country stories leaking into
    # unrelated briefs merely because they mention operational disruption.
    country_identified = country_explicit
    # A concrete natural hazard can be operationally important before a news
    # report names a port or airport. Keep it when the selected country is
    # explicit; the risk model still separates impact from evidential confidence.
    relevant = score >= 60 and country_identified and operational and (transport or primary or hazard)
    return {"score": min(100, score), "relevant": relevant, "reason": "; ".join(reasons) or "no direct operational connection identified"}


def _signal_corroboration(signal, articles, country):
    """Require event-language overlap, not merely a matching country name."""
    country_tokens = _tokens(country)
    signal_tokens = _tokens(
        f'{signal.get("event_type", "")} {signal.get("title", "")} {signal.get("summary", "")}'
    ) - country_tokens - SIGNAL_STOPWORDS
    best_overlap = 0
    supporting_articles = 0
    for article in articles:
        # Stored summaries may contain the collector's category label (for
        # example "conflicts and war"), so corroboration uses the publisher's
        # headline only to avoid circular confirmation.
        article_tokens = _tokens(article.get("headline", ""))
        overlap = len(signal_tokens & article_tokens)
        if overlap >= 2:
            supporting_articles += 1
            best_overlap = max(best_overlap, overlap)
    return supporting_articles, best_overlap


def _source_evidence(article):
    try:
        sources = json.loads(article.get("sources_json") or "[]")
    except (TypeError, json.JSONDecodeError):
        sources = []
    if not sources:
        return 1, 0, 1.0
    families = {}
    for source in sources:
        family = source.get("source_family") or (source.get("publisher") or "unknown").casefold()
        primary = source.get("source_type") in {
            "Port authority", "Carrier advisory", "Maritime authority", "Aviation authority", "Government notice"
        }
        families[family] = max(families.get(family, 1.0), 1.5 if primary else 1.0)
    primary_count = sum(weight > 1 for weight in families.values())
    return len(families), primary_count, sum(families.values())


def _published_age_days(article):
    for field in ("published_at", "observed_at", "collected_at"):
        value = article.get(field)
        if not value:
            continue
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return max(0.0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() / 86400)
        except (TypeError, ValueError):
            continue
    return None


def operational_connection(article, exposure=None):
    """Explainably link a report to assets, consequences, dependency and current evidence."""
    text = f'{article.get("headline", "")} {article.get("summary", "")}'
    lowered, tokens = text.casefold(), _tokens(text)
    asset_hits = sorted(tokens & ASSET_TERMS)
    # Retain explicit named infrastructure where publisher wording provides it.
    named_assets = sorted(set(re.findall(
        r"\b(?:[A-Z][\w'’-]+\s+){1,4}(?:Port|Harbou?r|Airport|Terminal|Canal|Strait|Bridge|Refinery|Pipeline|Railway)\b",
        text,
    )))
    routes = sorted(route for route in STRATEGIC_ROUTE_MARKERS if route in lowered)
    country = article.get("country", "")
    locations = ([country] if country and country_mentioned(text, country) else []) + routes
    asset_location = min(100, (55 if asset_hits else 0) + (25 if named_assets else 0) + (20 if locations else 0))

    consequence_hits = sorted(tokens & OPERATIONAL_CONSEQUENCE_TERMS)
    inferred_threat = bool(tokens & ARMED_THREAT_TERMS) and bool(asset_hits or routes)
    consequence = min(100, 25 + len(consequence_hits) * 18) if consequence_hits else 35 if inferred_threat else 0

    sources, primary_sources, _ = _source_evidence(article)
    support = 100 if primary_sources and sources >= 2 else 80 if primary_sources else 70 if sources >= 2 else 35

    age_days = _published_age_days(article)
    if tokens & RESOLUTION_TERMS:
        lifecycle, temporal = "Recovering / resolved", 25
    elif age_days is not None and age_days > 30:
        lifecycle, temporal = "Historical / stale", 10
    elif tokens & ACTIVE_STATUS_TERMS or (age_days is not None and age_days <= 3):
        lifecycle, temporal = "Active", 100 if age_days is None or age_days <= 1 else 85
    elif tokens & DEVELOPING_STATUS_TERMS:
        lifecycle, temporal = "Developing", 70
    elif age_days is not None and age_days <= 7:
        lifecycle, temporal = "Recently reported", 65
    else:
        lifecycle, temporal = "Status unconfirmed", 40

    dependency = company_exposure_score(exposure)
    if dependency is None:
        components = {"asset_location": asset_location, "consequence": consequence,
                      "source_support": support, "temporal_relevance": temporal}
        weights = {"asset_location": .3125, "consequence": .375,
                   "source_support": .1875, "temporal_relevance": .125}
        model = "Generic operational connection"
    else:
        components = {"asset_location": asset_location, "consequence": consequence,
                      "company_dependency": dependency, "source_support": support,
                      "temporal_relevance": temporal}
        weights = {"asset_location": .25, "consequence": .30, "company_dependency": .20,
                   "source_support": .15, "temporal_relevance": .10}
        model = "Company-specific operational connection"
    raw_score = round(sum(components[key] * weights[key] for key in components))
    gate_passed = asset_location >= 50 and consequence >= 50 and support >= 70
    score = raw_score if gate_passed else min(raw_score, 59)
    missing = []
    if asset_location < 50: missing.append("identifiable asset, route or location")
    if consequence < 50: missing.append("concrete operational consequence")
    if support < 70: missing.append("official or independent confirmation")
    return {
        "score": score, "raw_score": raw_score, "gate_passed": gate_passed,
        "strength": "Strong" if score >= 70 and gate_passed else "Moderate" if score >= 45 else "Weak",
        "model": model, "components": components, "weights": weights,
        "assets": named_assets or asset_hits, "locations": locations,
        "consequences": consequence_hits, "lifecycle": lifecycle, "age_days": age_days,
        "missing_evidence": missing,
    }


def assess_article(article, exposure=None):
    text = f'{article.get("headline", "")} {article.get("summary", "")} {article.get("category", "")}'
    lowered = text.casefold()
    tokens = _tokens(text)
    maritime = len(tokens & MARITIME_TERMS)
    aviation = len(tokens & AVIATION_TERMS)
    disruption = len(tokens & DISRUPTION_TERMS)
    critical = len(tokens & CRITICAL_TERMS)
    if maritime and aviation:
        mode = "Maritime and aviation"
    elif maritime:
        mode = "Maritime"
    elif aviation:
        mode = "Aviation"
    else:
        mode = "Multimodal / indirect"

    score = min(100, 12 + disruption * 10 + critical * 8 + min(12, maritime * 4 + aviation * 4))
    semantic = (_EMBEDDING_MODEL.scores(text) if _EMBEDDING_MODEL and _EMBEDDING_MODEL.trained
                else {"trained": False, "coverage": 0, "disruption": 0, "severity": 0, "transport": 0})
    # Semantic association is deliberately bounded. It can recognise wording
    # learned near disruption/transport concepts, but cannot independently
    # create a Critical assessment or replace corroborating evidence.
    semantic_bonus = 0
    if semantic["trained"] and semantic["coverage"] >= .25:
        semantic_bonus += round(max(0, semantic["disruption"] - .12) * 18)
        semantic_bonus += round(max(0, semantic["transport"] - .12) * 10)
        semantic_bonus += round(max(0, semantic["severity"] - .15) * 8)
        semantic_bonus = min(12, semantic_bonus)
        score = min(100, score + semantic_bonus)
    if article.get("category") == "Infrastructure and supply chains":
        score = min(100, score + 12)
    sources, primary_sources, evidence_weight = _source_evidence(article)
    operational_terms = tokens & OPERATIONAL_CONSEQUENCE_TERMS
    corroborated = sources >= 2
    armed_transport_threat = bool(tokens & ARMED_THREAT_TERMS) and bool(maritime or aviation)
    strategic_routes = sorted(route for route in STRATEGIC_ROUTE_MARKERS if route in lowered)
    inferred_route_exposure = armed_transport_threat and bool(strategic_routes or {"shipping", "ship", "vessel", "airspace", "airport"} & tokens)
    provisional = not operational_terms and not corroborated and primary_sources == 0 and not inferred_route_exposure
    provisional_reason = ""
    exposure_basis = "Confirmed operational consequence" if operational_terms else ""
    if inferred_route_exposure and not operational_terms:
        # Armed threats to shipping/aviation or a strategic route create a
        # credible anticipatory exposure even before a closure is announced.
        score = max(38, min(score, 69))
        exposure_basis = "Inferred armed-threat exposure to transport operations"
    severe_multimodal_shutdown = (
        corroborated and bool(tokens & ARMED_THREAT_TERMS) and maritime and aviation
        and len(operational_terms) >= 2
    )
    if severe_multimodal_shutdown:
        score = max(score, 78)
        exposure_basis = "Corroborated armed disruption affecting multiple transport modes"
    connection = operational_connection(article, exposure)
    # The connection model refines the lexical impact estimate, but cannot
    # manufacture a high score when its three evidence gates are absent.
    score = round(score * .70 + connection["score"] * .30)
    if not connection["gate_passed"] and score >= 70:
        score = 69
    if provisional:
        # Severe or geopolitical language can indicate a developing regional
        # situation, but without an operational consequence or independent
        # confirmation it is not enough for a Moderate country-operation risk.
        score = min(score, 37)
        provisional_reason = "No confirmed closure, delay, rerouting, cancellation, damage or comparable operational consequence; only one non-primary source is available."
        exposure_basis = "Unconfirmed indirect exposure"
    if score >= 70 and not corroborated and primary_sources == 0:
        score = 69
        exposure_basis = (exposure_basis or "Reported operational disruption") + "; Critical escalation withheld pending independent corroboration"
    confidence = min(95, round(42 + min(30, evidence_weight * 7) + min(18, disruption * 4)))
    uncertainty = max(5, 18 - min(10, round(evidence_weight * 2)))
    lower, upper = max(0, confidence - uncertainty), min(100, confidence + uncertainty)
    level = "Critical" if score >= 70 else "Moderate" if score >= 38 else "Low"

    effects = []
    if maritime: effects.append("possible port, ocean-freight, routing or lead-time disruption")
    if aviation: effects.append("possible air-cargo, airport, airspace or urgent-freight disruption")
    if "sanction" in tokens: effects.append("supplier, payment, customs or trade-compliance exposure")
    if tokens & {"earthquake", "flood", "storm", "cyclone", "wildfire"}: effects.append("possible facility, utility and inland-transport interruption")
    if tokens & {"war", "conflict", "attack", "protest", "strike"}: effects.append("possible workforce, security and cross-border movement constraints")
    if not effects: effects.append("monitor for indirect supplier, inventory and transport effects")
    return {"score": score, "level": level, "mode": mode, "confidence": confidence,
            "confidence_low": lower, "confidence_high": upper, "effects": effects,
            "source_count": sources, "primary_source_count": primary_sources,
            "provisional": provisional, "provisional_reason": provisional_reason,
            "operational_terms": sorted(operational_terms), "exposure_basis": exposure_basis,
            "strategic_routes": strategic_routes, "inferred_route_exposure": inferred_route_exposure,
            "embedding_method": "PPMI corpus embedding" if semantic["trained"] else "Lexicon fallback",
            "semantic_bonus": semantic_bonus, "semantic_scores": semantic,
            "operational_connection": connection,
            "tone": tone_assessment(f'{article.get("headline", "")} {article.get("summary", "")}'),
            "country_sentiment": country_sentiment(
                f'{article.get("headline", "")} {article.get("summary", "")}', article.get("country", "")
            )}


EXPOSURE_WEIGHTS = {
    "supplier_concentration": .25,
    "goods_value": .20,
    "route_dependency": .20,
    "inventory_vulnerability": .15,
    "customer_exposure": .10,
    "substitution_difficulty": .10,
}


def company_exposure_score(exposure):
    """Convert six stakeholder-entered 0–100 exposure inputs to one index."""
    if not exposure:
        return None
    return round(sum(
        max(0, min(100, float(exposure.get(field, 0) or 0))) * weight
        for field, weight in EXPOSURE_WEIGHTS.items()
    ))


def _event_likelihood(risk, sensor_status):
    """Estimate occurrence/operational likelihood separately from impact."""
    likelihood = 15
    if risk["operational_terms"] or risk["inferred_route_exposure"]:
        likelihood += 35
    if risk["source_count"] >= 2:
        likelihood += 20
    if risk["primary_source_count"] >= 1:
        likelihood += 15
    likelihood += min(15, max(0, risk["source_count"] - 1) * 5)
    if sensor_status == "Corroborated":
        likelihood += 10
    return min(100, likelihood)


def assess_country(country, articles, signals=None, exposure=None, anomaly_score=0):
    assessed = [{**article, "risk": assess_article(article, exposure)} for article in articles]
    # Country risk uses up to ten latest distinct consolidated events. The
    # interface may show only five for readability, so the returned metadata
    # explicitly reports the scoring-window size.
    assessed.sort(key=lambda item: item.get("published_at") or "", reverse=True)
    matching_signals = []
    country_key = country.casefold()
    for signal in signals or []:
        signal_place = f'{signal.get("country", "")} {signal.get("location", "")}'.casefold()
        if country_key in signal_place:
            matching_signals.append(signal)
    if not assessed:
        if matching_signals:
            severity_score = {"Critical": 85, "High": 62, "Watch": 34}
            strongest = max(matching_signals, key=lambda item: severity_score.get(item.get("severity"), 20))
            raw_signal_score = severity_score.get(strongest.get("severity"), 20)
            score = round(raw_signal_score * .35)
            level = "Critical" if score >= 70 else "Moderate" if score >= 38 else "Low"
            confidence = min(45, int(strongest.get("confidence") or 60))
            return {"country": country, "level": level, "score": score, "confidence": confidence,
                    "confidence_low": max(0, confidence - 12), "confidence_high": min(100, confidence + 8),
                    "mode": assess_article({"headline": strongest.get("title"), "summary": strongest.get("summary")})["mode"],
                    "summary": f'{strongest.get("source")} reports {strongest.get("title")}. This is a provisional operational signal because no independent country reporting currently corroborates it.',
                    "articles": [], "evidence_count": len(matching_signals), "sensor_status": "Provisional",
                    "sensor_reason": "No matching independent news evidence", "sensor_weight": 0,
                    "model": "Provisional sensor only", "components": {}, "component_weights": {},
                    "exposure_configured": exposure is not None}
        return {"country": country, "level": "Low", "score": 0, "confidence": 15,
                "confidence_low": 5, "confidence_high": 30, "mode": "Insufficient evidence",
                "summary": "No fresh country-specific reporting is stored. Refresh this country before treating the low marker as an assessment.",
                "articles": [], "evidence_count": 0, "sensor_status": "None", "sensor_reason": "No matching sensor", "sensor_weight": 0,
                "model": "Insufficient evidence", "components": {}, "component_weights": {},
                "exposure_configured": exposure is not None}
    top = assessed[:10]
    # Ten per cent exponential decay: each event receives 90% of the weight of
    # the event immediately before it.
    recency_weights = [.9 ** index for index in range(len(top))]
    total_weight = sum(recency_weights)
    recency_news_score = round(sum(item["risk"]["score"] * weight for item, weight in zip(top, recency_weights)) / total_weight)
    sensor_status, sensor_reason, sensor_weight = "None", "No matching sensor", 0
    strongest_sensor_severity, sensor_support_count = None, 0
    if matching_signals:
        severity_scores = {"Critical": 85, "High": 62, "Watch": 34}
        evaluated = []
        for signal in matching_signals:
            support_count, overlap = _signal_corroboration(signal, assessed, country)
            evaluated.append((support_count, overlap, severity_scores.get(signal.get("severity"), 20), signal))
        support_count, overlap, signal_score, strongest = max(evaluated, key=lambda item: (item[0] > 0, item[2], item[1]))
        strongest_sensor_severity = strongest.get("severity")
        sensor_support_count = support_count
        if support_count:
            sensor_weight = .25
            sensor_status = "Corroborated"
            sensor_reason = f'{strongest.get("source")} signal supported by {support_count} matching news report{"s" if support_count != 1 else ""}'
        else:
            sensor_weight = .05
            sensor_status = "Provisional"
            sensor_reason = f'{strongest.get("source")} signal has no matching independent news evidence'
    credible_items = [
        item for item in top
        if (item["risk"]["source_count"] >= 2 or item["risk"]["primary_source_count"] >= 1)
        and bool(item["risk"]["operational_terms"] or item["risk"]["inferred_route_exposure"])
    ]
    strongest_article = max(credible_items or top, key=lambda item: item["risk"]["score"])
    strongest_risk = strongest_article["risk"]
    # Weak, non-primary stories may inform monitoring but cannot contribute a
    # Moderate event component until corroborated or operationally confirmed.
    if credible_items:
        credible_positions = [(item, recency_weights[index]) for index, item in enumerate(top) if item in credible_items]
        credible_weight = sum(weight for _, weight in credible_positions)
        credible_mean = sum(item["risk"]["score"] * weight for item, weight in credible_positions) / credible_weight
        # Multiple distinct corroborated events indicate sustained or
        # concurrent disruption. The bounded uplift cannot be earned by weak
        # or single-source reports and is capped at 12 points.
        concurrency_bonus = min(12, max(0, round((credible_weight - 1) * 3)))
        credible_event = min(100, round(.70 * strongest_risk["score"] + .30 * credible_mean + concurrency_bonus))
    else:
        credible_mean, concurrency_bonus = 0, 0
        credible_event = min(37, strongest_risk["score"])
    likelihood_index = _event_likelihood(strongest_risk, sensor_status)
    likelihood_impact = round(likelihood_index * credible_event / 100)
    anomaly_component = round(max(0, min(100, float(anomaly_score or 0))))
    exposure_component = company_exposure_score(exposure)
    if exposure_component is None:
        component_weights = {"credible_event": .50, "likelihood_impact": .286, "historical_anomaly": .214}
        model = "Generic hybrid (company exposure not configured)"
        components = {"credible_event": credible_event, "likelihood_impact": likelihood_impact,
                      "historical_anomaly": anomaly_component}
    else:
        component_weights = {"credible_event": .35, "company_exposure": .30,
                             "likelihood_impact": .20, "historical_anomaly": .15}
        model = "Exposure-aware hybrid"
        components = {"credible_event": credible_event, "company_exposure": exposure_component,
                      "likelihood_impact": likelihood_impact, "historical_anomaly": anomaly_component}
    score = round(sum(components[name] * weight for name, weight in component_weights.items()))
    # The aggregate must not fall below a credible operational event visible in
    # the five-update decision set. This is a band floor, not a substitution of
    # the article score for the country score.
    credible_moderate_event = any(
        item["risk"]["level"] in {"Moderate", "Critical"}
        and (item["risk"]["source_count"] >= 2 or item["risk"]["primary_source_count"] >= 1)
        and bool(item["risk"]["operational_terms"] or item["risk"]["inferred_route_exposure"])
        for item in top
    )
    if credible_moderate_event:
        score = max(score, 38)
    elif sensor_status != "Corroborated":
        # A weighted average containing an unconfirmed Moderate headline must
        # not make the country Moderate. The country band requires at least one
        # corroborated/primary operational event or a corroborated sensor.
        score = min(score, 37)

    # Critical escalation is intentionally event-led rather than based only on
    # the five-story average. It still requires corroboration or multiple
    # concurrent high-impact stories, preventing sensational single-source
    # headlines from turning a country Critical.
    escalation_reason = ""
    top_three = sorted(top, key=lambda item: item["risk"]["score"], reverse=True)[:3]
    top_three_mean = round(sum(item["risk"]["score"] for item in top_three) / len(top_three))
    severe_corroborated_event = (
        strongest_risk["level"] == "Critical"
        and strongest_risk["score"] >= 70
        and strongest_risk["source_count"] >= 2
        and bool(strongest_risk["operational_terms"] or strongest_risk["inferred_route_exposure"])
    )
    concurrent_high_risk_events = (
        len(top_three) == 3
        and top_three_mean >= 65
        and all(
            (item["risk"]["source_count"] >= 2 or item["risk"]["primary_source_count"] >= 1)
            and bool(item["risk"]["operational_terms"] or item["risk"]["inferred_route_exposure"])
            for item in top_three
        )
    )
    corroborated_critical_sensor = (
        sensor_status == "Corroborated"
        and strongest_sensor_severity == "Critical"
        and sensor_support_count > 0
        and any(item["risk"]["operational_terms"] for item in top)
    )
    if severe_corroborated_event:
        score = max(score, 75)
        escalation_reason = "One of the ten latest distinct scoring events is Critical, identifies a concrete operational consequence and is corroborated by at least two independent source families."
    elif concurrent_high_risk_events:
        score = max(score, 70)
        escalation_reason = f"Three concurrent high-impact stories have an average operational score of {top_three_mean}/100."
    elif corroborated_critical_sensor:
        score = max(score, 72)
        escalation_reason = "A critical operational sensor is corroborated by reporting of a concrete disruption."
    confidence = round(sum(item["risk"]["confidence"] * weight for item, weight in zip(top, recency_weights)) / total_weight)
    uncertainty = max(5, 15 - min(8, len(assessed)))
    modes = {item["risk"]["mode"] for item in top}
    if "Maritime and aviation" in modes or ({"Maritime", "Aviation"} <= modes): mode = "Maritime and aviation"
    elif "Maritime" in modes: mode = "Maritime"
    elif "Aviation" in modes: mode = "Aviation"
    else: mode = "Multimodal / indirect"
    level = "Critical" if score >= 70 else "Moderate" if score >= 38 else "Low"
    headlines = "; ".join(item["headline"] for item in top[:3])
    return {"country": country, "level": level, "score": score, "confidence": confidence,
            "confidence_low": max(0, confidence - uncertainty), "confidence_high": min(100, confidence + uncertainty),
            "mode": mode, "summary": f"Latest reporting indicates {level.lower()} supply-chain risk for {country}. Principal signals: {headlines}",
            "articles": top, "evidence_count": len(top), "sensor_status": sensor_status,
            "sensor_reason": sensor_reason, "sensor_weight": round(sensor_weight * 100),
            "escalation_reason": escalation_reason, "top_three_mean": top_three_mean,
            "model": model, "components": components, "component_weights": component_weights,
            "exposure_configured": exposure_component is not None,
            "likelihood_index": likelihood_index, "hybrid_raw_score": round(sum(
                components[name] * weight for name, weight in component_weights.items()), 1),
            "recency_news_score": recency_news_score,
            "sensor_likelihood_bonus": 10 if sensor_status == "Corroborated" else 0,
            "scoring_window_size": len(top), "recency_decay": .90,
            "credible_event_count": len(credible_items),
            "credible_event_mean": round(credible_mean), "concurrency_bonus": concurrency_bonus}
