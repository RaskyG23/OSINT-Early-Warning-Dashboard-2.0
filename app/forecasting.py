"""Explainable short-horizon projections from country operational news.

This module forecasts the dashboard's operational-risk indicator, not the
occurrence of a particular real-world event. It deliberately uses a small,
auditable recency-weighted linear model because country histories are not yet
large enough to support an independently validated complex classifier.
"""

from collections import Counter
from datetime import datetime, timezone
from math import exp, log, sqrt

from app.supply_chain import assess_article


def _datetime(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _band(score):
    return "Critical" if score >= 70 else "Moderate" if score >= 38 else "Low"


def _weighted_regression(points):
    """Return slope (score points/day) and weighted residual RMSE."""
    if len(points) < 2:
        return 0.0, 18.0
    newest = max(point[0] for point in points)
    origin = min(point[0] for point in points)
    rows = []
    for observed, score in points:
        x = (observed - origin).total_seconds() / 86400
        age = max(0, (newest - observed).total_seconds() / 86400)
        weight = exp(-log(2) * age / 7)  # seven-day evidence half-life
        rows.append((x, float(score), weight))
    total_weight = sum(weight for _, _, weight in rows)
    mean_x = sum(x * weight for x, _, weight in rows) / total_weight
    mean_y = sum(y * weight for _, y, weight in rows) / total_weight
    denominator = sum(weight * (x - mean_x) ** 2 for x, _, weight in rows)
    slope = (sum(weight * (x - mean_x) * (y - mean_y) for x, y, weight in rows) / denominator
             if denominator else 0.0)
    intercept = mean_y - slope * mean_x
    rmse = sqrt(sum(weight * (y - (intercept + slope * x)) ** 2 for x, y, weight in rows) / total_weight)
    # Sparse news can create extreme slopes from two nearly simultaneous
    # reports. Bound the operational projection to five points per day.
    return max(-5.0, min(5.0, slope)), rmse


def forecast_country(country, articles, assessment, horizons=(3, 7)):
    assessed = []
    for article in articles:
        observed = _datetime(article.get("published_at") or article.get("collected_at"))
        if not observed:
            continue
        risk = article.get("risk") or assess_article(article)
        assessed.append({**article, "risk": risk, "observed": observed})
    assessed.sort(key=lambda item: item["observed"])
    if not assessed:
        return {
            "available": False, "country": country, "method": "Recency-weighted linear regression",
            "reason": "No fresh qualifying operational events are available for a defensible short-horizon projection.",
            "projections": [], "drivers": [],
        }

    points = [(item["observed"], item["risk"]["score"]) for item in assessed]
    slope, rmse = _weighted_regression(points)
    if slope >= 1.5:
        direction = "Escalating"
    elif slope <= -1.5:
        direction = "Easing"
    else:
        direction = "Broadly stable"
    baseline = float(assessment.get("score") or points[-1][1])
    evidence_count = len(assessed)
    corroborated = sum(
        item["risk"].get("source_count", 0) >= 2 or item["risk"].get("primary_source_count", 0) >= 1
        for item in assessed
    )
    mean_confidence = sum(item["risk"].get("confidence", 40) for item in assessed) / evidence_count
    confidence = round(max(20, min(82,
        .55 * mean_confidence + min(18, evidence_count * 3) + min(12, corroborated * 3) - min(15, rmse / 2)
    )))
    uncertainty = round(max(8, min(30, 8 + rmse + 12 / sqrt(evidence_count))))
    projections = []
    for days in horizons:
        score = round(max(0, min(100, baseline + slope * days)))
        # Uncertainty expands with horizon but remains an indicator range, not
        # a statistically calibrated prediction interval.
        spread = min(35, round(uncertainty * sqrt(days / min(horizons))))
        projections.append({
            "days": days, "score": score, "level": _band(score),
            "low": max(0, score - spread), "high": min(100, score + spread),
        })
    drivers = sorted(assessed, key=lambda item: (item["risk"]["score"], item["observed"]), reverse=True)[:3]
    scenario_counts = Counter(
        effect
        for item in assessed
        for effect in item["risk"].get("effects", [])
        if effect and not effect.startswith("monitor for indirect")
    )
    scenarios = [
        {"outcome": outcome, "supporting_events": count}
        for outcome, count in scenario_counts.most_common(4)
    ]
    if direction == "Escalating":
        action = "Review exposed suppliers and transport routes now; prepare alternatives and verify whether the strongest reported consequences are continuing."
    elif direction == "Easing":
        action = "Continue monitoring before standing down contingencies; verify that services, infrastructure and routes have actually recovered."
    else:
        action = "Maintain monitoring and confirm supplier, inventory and transport status if the projected band remains Moderate or Critical."
    return {
        "available": True, "country": country, "method": "Recency-weighted linear regression",
        "direction": direction, "slope": round(slope, 2), "confidence": confidence,
        "evidence_count": evidence_count, "corroborated_count": corroborated,
        "rmse": round(rmse, 2), "projections": projections, "drivers": drivers,
        "scenarios": scenarios, "action": action,
    }
