import math
from datetime import datetime, timedelta, timezone


COMMERCIAL_VESSEL_TYPES = {
    "cargo", "tanker", "passenger", "container", "bulk carrier", "vehicle carrier",
    "chemical tanker", "lng tanker", "lpg tanker", "ro-ro", "ferry",
}

# ICAO operator prefixes for widely used dedicated freight/express operators.
# This is intentionally conservative: passenger belly cargo is not inferable
# from an ADS-B state vector and is therefore excluded.
CARGO_CALLSIGN_PREFIXES = {
    "ABW": "AirBridgeCargo", "AHK": "Air Hong Kong", "ATN": "Air Transport International",
    "BOX": "AeroLogic", "CAO": "Air China Cargo", "CLX": "Cargolux",
    "CKS": "Kalitta Air", "FDX": "FedEx Express", "GTI": "Atlas Air",
    "LCF": "Boeing Dreamlifter", "LCO": "LATAM Cargo",
    "MNB": "MNG Airlines Cargo", "NCR": "National Airlines Cargo", "PAC": "Polar Air Cargo",
    "SOO": "Southern Air", "SRR": "Star Air",
    "SQC": "Singapore Airlines Cargo", "TPA": "Transcarga", "UPS": "UPS Airlines",
}
CARGO_OPERATOR_MARKERS = {
    "cargo", "freight", "express", "logistics", "air transport international",
    "atlas air", "cargolux", "kalitta", "polar air", "aerologic",
}


def age_minutes(record, now=None):
    now = now or datetime.now(timezone.utc)
    for field in ("observed_at", "collected_at"):
        try:
            value = datetime.fromisoformat(str(record.get(field) or "").replace("Z", "+00:00"))
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return max(0, (now - value.astimezone(timezone.utc)).total_seconds() / 60)
        except (TypeError, ValueError):
            continue
    return float("inf")


def likely_commercial_aircraft(record):
    """Operational heuristic; OpenSky state vectors do not expose operator class."""
    callsign = (record.get("callsign") or "").strip().upper()
    return not bool(record.get("on_ground")) and len(callsign) >= 3 and not callsign.startswith("N/A")


def cargo_flight_assessment(record, route=None):
    """Identify likely dedicated freighters without claiming knowledge of manifests."""
    route = route or {}
    callsign = (record.get("callsign") or "").strip().upper()
    prefix = callsign[:3]
    airline = (route.get("airline") or "").strip()
    airline_lower = airline.casefold()
    if prefix in CARGO_CALLSIGN_PREFIXES:
        return {"cargo": True, "confidence": "High", "basis":
                f"Callsign prefix {prefix} is associated with {CARGO_CALLSIGN_PREFIXES[prefix]} freight operations"}
    marker = next((value for value in CARGO_OPERATOR_MARKERS if value in airline_lower), None)
    if marker:
        return {"cargo": True, "confidence": "High", "basis":
                f'Route metadata identifies freight operator {airline}'}
    return {"cargo": False, "confidence": "Insufficient", "basis":
            "No dedicated-freight operator evidence in the available callsign or route metadata"}


def likely_commercial_vessel(record):
    """Prefer AIS ship class; fall back to a named, moving vessel."""
    vessel_type = (record.get("vessel_type") or "").strip().casefold()
    if any(marker in vessel_type for marker in COMMERCIAL_VESSEL_TYPES):
        return True
    name = (record.get("ship_name") or "").strip()
    return bool(name and not name.upper().startswith("MMSI ") and float(record.get("speed_knots") or 0) >= 0.5)


def active_records(records, freshness_minutes=30, now=None):
    return [record for record in records if age_minutes(record, now=now) <= freshness_minutes]


def great_circle_km(lat1, lon1, lat2, lon2):
    values = [lat1, lon1, lat2, lon2]
    if any(value is None for value in values):
        return None
    lat1, lon1, lat2, lon2 = map(math.radians, map(float, values))
    delta_lat, delta_lon = lat2 - lat1, lon2 - lon1
    a = math.sin(delta_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    return 6371.0 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def flight_estimates(position, route, now=None):
    """Approximate block duration and ETA; never presented as airline schedule data."""
    now = now or datetime.now(timezone.utc)
    route_km = great_circle_km(route.get("origin_latitude"), route.get("origin_longitude"),
                               route.get("destination_latitude"), route.get("destination_longitude"))
    remaining_km = great_circle_km(position.get("latitude"), position.get("longitude"),
                                   route.get("destination_latitude"), route.get("destination_longitude"))
    if route_km is None or remaining_km is None:
        return {"route_km": None, "duration_minutes": None, "remaining_km": None, "eta": None}
    # Great-circle cruise estimate plus taxi/climb/descent allowance.
    duration_minutes = round(route_km / 800 * 60 + 35)
    observed_speed_kmh = float(position.get("velocity_knots") or 0) * 1.852
    estimating_speed = max(500, min(950, observed_speed_kmh)) if observed_speed_kmh else 800
    remaining_minutes = round(remaining_km / estimating_speed * 60 + 15)
    return {"route_km": round(route_km), "duration_minutes": duration_minutes,
            "remaining_km": round(remaining_km), "eta": now + timedelta(minutes=remaining_minutes)}


def format_duration(minutes):
    if minutes is None:
        return "Unavailable"
    hours, mins = divmod(int(minutes), 60)
    return f"{hours}h {mins:02d}m" if hours else f"{mins}m"


def country_relationship(route, selected_country, position_in_country=True):
    """Explain why a route belongs to a selected-country monitoring view."""
    aliases = {"united states of america": "united states", "russian federation": "russia",
               "viet nam": "vietnam", "iran, islamic republic of": "iran"}
    normalise = lambda value: aliases.get((value or "").strip().casefold(), (value or "").strip().casefold())
    selected = normalise(selected_country)
    origin_match = normalise(route.get("origin_country")) == selected
    destination_match = normalise(route.get("destination_country")) == selected
    if origin_match and destination_match:
        return "Domestic flight within selected country"
    if origin_match:
        return "Departing from selected country"
    if destination_match:
        return "Arriving in selected country"
    if position_in_country:
        return "Transiting selected-country monitoring area"
    return None


def resolve_aircraft_click(click, aircraft):
    """Resolve plotly-events payloads by index, customdata or coordinates."""
    if not click or not aircraft:
        return None
    point = click.get("pointNumber", click.get("pointIndex"))
    if isinstance(point, int) and 0 <= point < len(aircraft):
        return aircraft[point]
    custom = click.get("customdata")
    if custom:
        match = next((item for item in aircraft if item.get("icao24") == custom), None)
        if match:
            return match
    lat = click.get("lat", click.get("y"))
    lon = click.get("lon", click.get("x"))
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    return min(aircraft, key=lambda item: (float(item["latitude"]) - lat) ** 2
               + (float(item["longitude"]) - lon) ** 2)


def resolve_vessel_click(click, vessels):
    """Resolve a vessel marker click by MMSI first, then trace index or coordinates."""
    if not click or not vessels:
        return None
    custom = str(click.get("customdata") or "").strip()
    if custom:
        match = next((item for item in vessels if str(item.get("mmsi") or "") == custom), None)
        if match:
            return match
    point = click.get("pointNumber", click.get("pointIndex"))
    if isinstance(point, int) and 0 <= point < len(vessels):
        return vessels[point]
    lat = click.get("lat", click.get("y"))
    lon = click.get("lon", click.get("x"))
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    return min(vessels, key=lambda item: (float(item["latitude"]) - lat) ** 2
               + (float(item["longitude"]) - lon) ** 2)


def route_path(position, route):
    """Return origin-current-destination coordinates when route metadata is complete."""
    values = (route.get("origin_latitude"), route.get("origin_longitude"),
              position.get("latitude"), position.get("longitude"),
              route.get("destination_latitude"), route.get("destination_longitude"))
    if any(value is None for value in values):
        return None
    return {
        "latitudes": [float(values[0]), float(values[2]), float(values[4])],
        "longitudes": [float(values[1]), float(values[3]), float(values[5])],
    }
