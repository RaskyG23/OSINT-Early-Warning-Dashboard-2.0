import json
import re
from datetime import datetime, timezone

MARITIME_TERMS = {"port", "shipping", "ship", "vessel", "maritime", "harbour", "harbor", "canal", "strait", "freight", "container", "dock", "tanker"}
AVIATION_TERMS = {"airport", "airspace", "aircraft", "aviation", "airline", "flight", "runway", "airfreight", "drone", "missile", "no-fly"}
DISRUPTION_TERMS = {"strike", "closure", "close", "closed", "blocked", "delay", "delayed", "disruption", "disrupted", "shortage", "sanction", "attack", "attacks", "war", "conflict", "flood", "earthquake", "wildfire", "storm", "cyclone", "outage", "blackout", "protest", "halt", "halted", "halting", "suspended", "damaged", "rerouted", "cancelled", "canceled"}
CRITICAL_TERMS = {"war", "attack", "attacks", "missile", "close", "closed", "closure", "blocked", "earthquake", "cyclone", "sanction", "outage", "halted", "halting", "suspended", "damaged"}
OPERATIONAL_CONSEQUENCE_TERMS = {
    "closure", "closed", "blocked", "delay", "delayed", "disruption", "disrupted", "shortage",
    "suspension", "suspended", "cancelled", "canceled", "cancellation", "reroute", "rerouted",
    "rerouting", "diverted", "diversion", "congestion", "backlog", "damage", "damaged", "outage",
    "blackout", "grounded", "halted", "stopped", "shutdown", "strike", "sanction", "restricted",
    "restriction", "embargo", "seized",
}
ARMED_THREAT_TERMS = {"war", "attack", "attacks", "missile", "drone", "conflict", "hostilities", "houthi", "seized"}
STRATEGIC_ROUTE_MARKERS = {
    "bab el-mandeb", "bab-el-mandeb", "red sea", "suez", "hormuz", "persian gulf", "gulf of aden",
    "black sea", "malacca", "panama canal", "english channel", "bosporus", "bosphorus",
}
POSITIVE_TONE_TERMS = {"reopens", "reopened", "resume", "resumes", "recovery", "restored", "agreement", "deal", "growth", "improves", "improved", "boost", "expands", "investment", "surplus", "eases"}
NEGATIVE_TONE_TERMS = DISRUPTION_TERMS | {"crisis", "risk", "threat", "fatalities", "killed", "loss", "decline", "collapse", "escalates", "impasse", "volatile", "warning"}
SIGNAL_STOPWORDS = {
    "a", "an", "the", "and", "or", "in", "on", "at", "to", "for", "of", "from", "with", "by",
    "signal", "near", "general", "reports", "reported", "event", "alert", "country", "identified",
    "referenced", "mentions", "articles", "sources", "gdelt",
}


def _tokens(text):
    return set(re.findall(r"[a-z]+(?:-[a-z]+)?", (text or "").lower()))


def tone_assessment(text):
    """Transparent lexicon tone score from -100 (negative) to +100 (positive)."""
    tokens = _tokens(text)
    positive = len(tokens & POSITIVE_TONE_TERMS)
    negative = len(tokens & NEGATIVE_TONE_TERMS)
    score = round(max(-100, min(100, (positive - negative) / max(1, positive + negative) * 100)))
    label = "Positive" if score >= 25 else "Negative" if score <= -25 else "Neutral"
    return {"score": score, "label": label, "positive_hits": positive, "negative_hits": negative}


def country_supply_chain_relevance(article, country):
    """Score evidence that a story affects the selected country's operations."""
    headline = article.get("headline", "")
    text = f'{headline} {article.get("summary", "")}'
    lowered = text.casefold()
    tokens = _tokens(text)
    headline_lowered = headline.casefold()
    headline_token_set = _tokens(headline)
    country_tokens = _tokens(country)
    country_phrase = country.casefold() in headline_lowered
    country_overlap = len(country_tokens & headline_token_set) == len(country_tokens) if country_tokens else False
    transport = bool(tokens & (MARITIME_TERMS | AVIATION_TERMS | {"road", "rail", "truck", "border", "customs", "warehouse", "supplier", "cargo", "logistics"}))
    operational = bool(tokens & (OPERATIONAL_CONSEQUENCE_TERMS | ARMED_THREAT_TERMS))
    primary = article.get("coverage_scope") == "Primary operational"
    local = article.get("coverage_scope") == "Local / regional"
    strategic = any(marker in lowered for marker in STRATEGIC_ROUTE_MARKERS)
    score = (45 if country_phrase or country_overlap else 0) + (30 if operational else 0) + (20 if transport else 0)
    score += 10 if primary else 5 if local else 0
    score += 5 if strategic else 0
    reasons = []
    if country_phrase or country_overlap: reasons.append("selected country is explicitly identified")
    if operational: reasons.append("an operational threat or consequence is described")
    if transport: reasons.append("transport or logistics exposure is identified")
    if primary: reasons.append("reported by a primary operational source")
    relevant = score >= 60 and operational and (transport or primary)
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


def assess_article(article):
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
            "tone": tone_assessment(f'{article.get("headline", "")} {article.get("summary", "")}')}


def assess_country(country, articles, signals=None):
    assessed = [{**article, "risk": assess_article(article)} for article in articles]
    assessed.sort(key=lambda item: (item["risk"]["score"], item.get("published_at") or ""), reverse=True)
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
                    "sensor_reason": "No matching independent news evidence", "sensor_weight": 0}
        return {"country": country, "level": "Low", "score": 0, "confidence": 15,
                "confidence_low": 5, "confidence_high": 30, "mode": "Insufficient evidence",
                "summary": "No fresh country-specific reporting is stored. Refresh this country before treating the low marker as an assessment.",
                "articles": [], "evidence_count": 0, "sensor_status": "None", "sensor_reason": "No matching sensor", "sensor_weight": 0}
    top = assessed[:5]
    score = round(sum(item["risk"]["score"] for item in top) / len(top))
    score = min(100, score + min(15, len(assessed) // 3))
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
        score = round(score * (1 - sensor_weight) + signal_score * sensor_weight)
    # A high volume of individually low-risk stories plus an unconfirmed sensor
    # must not create a stakeholder-facing Moderate rating. At least one
    # Moderate/Critical article or a corroborated operational sensor is required.
    if sensor_status != "Corroborated" and all(item["risk"]["level"] == "Low" for item in top):
        score = min(score, 37)

    # Critical escalation is intentionally event-led rather than based only on
    # the five-story average. It still requires corroboration or multiple
    # concurrent high-impact stories, preventing sensational single-source
    # headlines from turning a country Critical.
    escalation_reason = ""
    strongest_article = top[0]
    strongest_risk = strongest_article["risk"]
    top_three = top[:3]
    top_three_mean = round(sum(item["risk"]["score"] for item in top_three) / len(top_three))
    severe_corroborated_event = (
        strongest_risk["score"] >= 75
        and strongest_risk["source_count"] >= 2
        and bool(strongest_risk["operational_terms"] or strongest_risk["inferred_route_exposure"])
    )
    concurrent_high_risk_events = len(top_three) == 3 and top_three_mean >= 65
    corroborated_critical_sensor = (
        sensor_status == "Corroborated"
        and strongest_sensor_severity == "Critical"
        and sensor_support_count > 0
        and any(item["risk"]["operational_terms"] for item in top)
    )
    if severe_corroborated_event:
        score = max(score, 75)
        escalation_reason = "A severe operational event is corroborated by at least two independent source families."
    elif concurrent_high_risk_events:
        score = max(score, 70)
        escalation_reason = f"Three concurrent high-impact stories have an average operational score of {top_three_mean}/100."
    elif corroborated_critical_sensor:
        score = max(score, 72)
        escalation_reason = "A critical operational sensor is corroborated by reporting of a concrete disruption."
    confidence = round(sum(item["risk"]["confidence"] for item in top) / len(top))
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
            "articles": assessed, "evidence_count": len(assessed), "sensor_status": sensor_status,
            "sensor_reason": sensor_reason, "sensor_weight": round(sensor_weight * 100),
            "escalation_reason": escalation_reason, "top_three_mean": top_three_mean}
