import json


HAZARD_CODES = {
    "Earthquake": "EQ", "Tropical cyclone": "TC", "Flood": "FL",
    "Volcano": "VO", "Drought": "DR", "Wildfire": "WF",
}
HAZARD_LABELS = {
    "EQ": "Earthquake", "TC": "Tropical cyclone", "FL": "Flood",
    "VO": "Volcano", "DR": "Drought", "WF": "Wildfire",
}
HAZARD_SYMBOLS = {
    "EQ": "diamond", "TC": "circle", "FL": "square",
    "VO": "triangle-up", "DR": "hexagon", "WF": "star",
}
ALERT_COLOURS = {"green": "#22c55e", "orange": "#f97316", "red": "#ef4444"}
ALERT_SCORES = {"green": 0.6, "orange": 1.8, "red": 3.0}


def disaster_details(signal):
    try:
        feature = json.loads(signal.get("raw_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        feature = {}
    properties = feature.get("properties") if isinstance(feature, dict) else {}
    properties = properties if isinstance(properties, dict) else {}
    event_type = str(properties.get("eventtype") or HAZARD_CODES.get(signal.get("event_type"), "other")).upper()
    alert = str(properties.get("alertlevel") or {
        "Critical": "red", "High": "orange", "Watch": "green"
    }.get(signal.get("severity"), "green")).lower()
    severity_data = properties.get("severitydata") if isinstance(properties.get("severitydata"), dict) else {}
    urls = properties.get("url") if isinstance(properties.get("url"), dict) else {}
    affected = properties.get("affectedcountries") if isinstance(properties.get("affectedcountries"), list) else []
    affected_names = [item.get("countryname") for item in affected if isinstance(item, dict) and item.get("countryname")]
    magnitude = severity_data.get("severity") if event_type == "EQ" else None
    return {
        "event_type": event_type,
        "event_label": HAZARD_LABELS.get(event_type, signal.get("event_type") or "Other hazard"),
        "symbol": HAZARD_SYMBOLS.get(event_type, "circle"),
        "alert": alert,
        "colour": ALERT_COLOURS.get(alert, ALERT_COLOURS["green"]),
        "alert_score": ALERT_SCORES.get(alert, ALERT_SCORES["green"]),
        "event_id": properties.get("eventid"),
        "episode_id": properties.get("episodeid"),
        "country": properties.get("country") or signal.get("country") or signal.get("location"),
        "affected_countries": affected_names,
        "from_date": properties.get("fromdate"),
        "to_date": properties.get("todate"),
        "modified": properties.get("datemodified"),
        "severity_text": severity_data.get("severitytext"),
        "magnitude": magnitude,
        "report_url": urls.get("report") or signal.get("source_url"),
        "geometry_url": urls.get("geometry"),
        "is_current": properties.get("iscurrent"),
    }
