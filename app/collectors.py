import hashlib
import html
import io
import json
import os
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import feedparser
import pycountry
import requests
import websocket

from app.database import (flight_routes, recent_signals, record_country_refresh, record_run, upsert_aircraft_states,
                          upsert_articles, upsert_flight_route, upsert_signals, upsert_vessel_positions)
from app.news_taxonomy import classify_general_news
from app.patterns import (analyze_observation, event_match, fact_variance, same_story,
                          story_key, strip_publisher_suffix)
from app.supply_chain import country_supply_chain_relevance, tone_assessment

UTC = timezone.utc
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "OSINTEarlyWarning-Docker/2.0"})

CATEGORIES = {
    "Conflicts and war": ["war conflict military", "attack security ceasefire", "defence armed forces"],
    "Politics and governance": ["politics government", "election parliament", "president minister policy"],
    "Economy and markets": ["economy markets", "inflation trade business", "investment sanctions finance"],
    "Environment and hazards": ["earthquake tsunami", "flood wildfire", "cyclone hurricane typhoon", "volcano drought landslide"],
    "Infrastructure and supply chains": ["infrastructure energy", "transport port shipping", "power supply chain"],
}

HAZARD_TERMS = {
    "earthquake": ("earthquake", "seismic", "tremor", "aftershock"), "tsunami": ("tsunami",),
    "flood": ("flood", "flooding"), "wildfire": ("wildfire", "forest fire", "bushfire"),
    "cyclone": ("cyclone", "hurricane", "typhoon", "tropical storm"),
    "volcano": ("volcano", "volcanic", "eruption"), "drought": ("drought",),
    "landslide": ("landslide", "mudslide"),
}

COUNTRY_ALIASES = {
    "United States of America": "US", "United States": "US", "United Kingdom": "UK",
    "Russian Federation": "Russia", "United Arab Emirates": "UAE",
    "Democratic Republic of the Congo": "DR Congo", "Côte d'Ivoire": "Ivory Coast",
    "Czechia": "Czech Republic", "Türkiye": "Turkey", "Cabo Verde": "Cape Verde",
    "Timor-Leste": "East Timor", "Eswatini": "Swaziland", "North Korea": "DPRK",
    "South Korea": "South Korea", "Laos": "Laos", "Palestine": "Palestine",
}

COUNTRY_LOOKUP_ALIASES = {
    "Bolivia": "BO", "Brunei": "BN", "Democratic Republic of the Congo": "CD",
    "Iran": "IR", "Laos": "LA", "Moldova": "MD", "North Korea": "KP",
    "Palestine": "PS", "Russia": "RU", "South Korea": "KR", "Syria": "SY",
    "Taiwan": "TW", "Tanzania": "TZ", "Venezuela": "VE", "Vietnam": "VN",
}

SOURCE_COUNTRIES = {
    "reuters": "United Kingdom", "associated press": "United States", "ap news": "United States",
    "bbc": "United Kingdom", "the guardian": "United Kingdom", "financial times": "United Kingdom",
    "sky news": "United Kingdom", "the telegraph": "United Kingdom", "cnn": "United States",
    "fox news": "United States", "new york times": "United States", "washington post": "United States",
    "bloomberg": "United States", "cnbc": "United States", "nbc news": "United States",
    "abc news": "United States", "cbs news": "United States", "al jazeera": "Qatar",
    "france 24": "France", "afp": "France", "le monde": "France", "deutsche welle": "Germany",
    "der spiegel": "Germany", "euronews": "France", "xinhua": "China", "china daily": "China",
    "cgtn": "China", "times of india": "India", "the hindu": "India", "ndtv": "India",
    "japan times": "Japan", "nhk": "Japan", "tass": "Russia", "kyiv independent": "Ukraine",
    "ukrinform": "Ukraine", "haaretz": "Israel", "times of israel": "Israel",
    "tehran times": "Iran", "irna": "Iran", "daily maverick": "South Africa",
    "news24": "South Africa", "vanguard": "Nigeria", "premium times": "Nigeria",
}

WIRE_FAMILIES = {
    "reuters": "reuters", "associated press": "associated-press", "ap news": "associated-press",
    "agence france-presse": "afp", "afp": "afp", "bloomberg": "bloomberg",
}

# These country-code domains are widely marketed internationally and therefore
# do not provide reliable evidence of a publisher's editorial home country.
GENERIC_CCTLDS = {"ai", "co", "fm", "io", "me", "tv", "ws"}

# Direct organisational domains are searched separately from general news. These
# records are labelled as primary evidence and remain distinguishable throughout
# clustering, storage and confidence assessment.
OPERATIONAL_SOURCE_GROUPS = {
    "Port authority": [
        "portoflosangeles.org", "polb.com", "panynj.gov", "porthouston.com",
        "portofrotterdam.com", "portofantwerpbruges.com", "mpa.gov.sg",
        "portauthoritynsw.com.au", "transnetnationalportsauthority.net",
    ],
    "Carrier advisory": [
        "maersk.com", "msc.com", "cma-cgm.com", "hapag-lloyd.com",
    ],
    "Maritime authority": [
        "ukmto.org", "navcen.uscg.gov", "maritime.dot.gov", "imo.org", "gov.uk",
    ],
    "Aviation authority": [
        "faa.gov", "eurocontrol.int", "easa.europa.eu", "icao.int",
    ],
    "Maritime specialist": [
        "gcaptain.com", "maritime-executive.com", "splash247.com", "safety4sea.com",
        "marinelink.com", "hellenicshippingnews.com", "porttechnology.org",
    ],
    "Aviation specialist": [
        "aircargonews.net", "flightglobal.com", "simpleflying.com", "aviationweek.com",
    ],
    "Logistics specialist": [
        "freightwaves.com", "theloadstar.com", "supplychaindive.com",
    ],
}


def _source_family(publisher, source_url):
    identity = f"{publisher or ''} {strip_publisher_suffix(publisher or '')}".casefold()
    for marker, family in WIRE_FAMILIES.items():
        if re.search(rf"\b{re.escape(marker)}\b", identity):
            return family
    host = urlparse(source_url or "").netloc.lower().removeprefix("www.")
    if host:
        parts = host.split(".")
        return ".".join(parts[-2:]) if len(parts) > 1 else host
    return re.sub(r"[^a-z0-9]+", "-", (publisher or "unknown").lower()).strip("-")


def now_iso():
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def severity_rank(level):
    return {"Watch": 1, "High": 2, "Critical": 3}.get(level, 0)


def _signal(**kwargs):
    kwargs.setdefault("collected_at", now_iso())
    kwargs.setdefault("country", kwargs.get("location", "Global"))
    kwargs.setdefault("raw_json", "{}")
    return kwargs


def collect_usgs():
    provider = "USGS"; collected = now_iso()
    try:
        url = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.geojson"
        data = SESSION.get(url, timeout=15).json(); rows = []
        for item in data.get("features", [])[:20]:
            p = item["properties"]; lon, lat, *_ = item["geometry"]["coordinates"]
            mag = float(p.get("mag") or 0); severity = "Critical" if mag >= 6 else "High" if mag >= 5 else "Watch"
            observed = datetime.fromtimestamp(p["time"] / 1000, UTC).isoformat()
            rows.append(_signal(id=f"usgs-{item['id']}", source=provider, event_type="Seismic", title=p["title"],
                location=p.get("place") or "Unknown", latitude=lat, longitude=lon, severity=severity, confidence=98,
                summary=f"USGS recorded a magnitude {mag:.1f} earthquake near {p.get('place') or 'the reported location'}.",
                outlook="Monitor official shaking, tsunami and infrastructure advisories.", source_url=p["url"],
                source_name="USGS", observed_at=observed, raw_json=json.dumps(item)))
        upsert_signals(rows); record_run(provider, "live", len(rows), "", collected); return rows
    except Exception as exc:
        record_run(provider, "unavailable", 0, str(exc), collected); return []


def collect_gdacs():
    provider = "GDACS"; collected = now_iso()
    url = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
    event_types = ("EQ", "TC", "FL", "VO", "DR", "WF")
    labels = {"EQ":"Earthquake","TC":"Tropical cyclone","FL":"Flood",
              "VO":"Volcano","DR":"Drought","WF":"Wildfire"}
    rows_by_id = {}; failures = []
    # Query each type independently. A combined request is dominated by common
    # earthquakes and wildfires and can omit sparse cyclones or volcanoes.
    for requested_type in event_types:
        try:
            response = SESSION.get(url, params={
                "eventlist": requested_type, "alertlevel": "Green;Orange;Red", "pagesize": 100,
            }, timeout=20)
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            failures.append(f"{requested_type}: {exc}")
            continue
        for item in data.get("features", []):
            if not isinstance(item, dict) or item.get("geometry", {}).get("type") != "Point":
                continue
            p = item.get("properties") or {}
            current = p.get("iscurrent")
            if current is False or str(current).strip().lower() == "false":
                continue
            coordinates = item.get("geometry", {}).get("coordinates") or []
            if len(coordinates) < 2:
                continue
            try:
                lon, lat = float(coordinates[0]), float(coordinates[1])
            except (TypeError, ValueError):
                continue
            event_type = str(p.get("eventtype") or requested_type).upper()
            event_id = p.get("eventid")
            if event_id is None:
                continue
            alert = str(p.get("alertlevel") or "green").lower()
            severity = "Critical" if alert == "red" else "High" if alert == "orange" else "Watch"
            urls = p.get("url") if isinstance(p.get("url"), dict) else {}
            report = urls.get("report") or "https://www.gdacs.org/"
            description = re.sub(r"<[^>]+>", " ", p.get("htmldescription") or p.get("description") or "")
            title = p.get("eventname") or p.get("name") or p.get("description") or f"{labels.get(event_type, 'Hazard')} alert"
            signal_id = f"gdacs-{event_type}-{event_id}"
            rows_by_id[signal_id] = _signal(id=signal_id, source=provider,
                event_type=labels.get(event_type, event_type), title=title,
                location=p.get("country") or "Global", latitude=lat, longitude=lon,
                severity=severity, confidence=95,
                summary=f"GDACS {alert} alert: {' '.join(description.split())}".strip(),
                outlook="Review the official GDACS report, affected countries, event dates and available footprint geometry.",
                source_url=report, source_name="GDACS",
                # The API contains only active events. Refreshing observed_at
                # records continued feed presence while true onset/update dates
                # remain traceable inside raw_json.
                observed_at=collected, raw_json=json.dumps(item))
    rows = list(rows_by_id.values())
    if rows or len(failures) < len(event_types):
        upsert_signals(rows)
        message = f"Partial collection: {'; '.join(failures)}" if failures else ""
        record_run(provider, "live", len(rows), message, collected)
        return rows
    record_run(provider, "unavailable", 0, "; ".join(failures), collected)
    return []


def collect_gdelt():
    provider = "GDELT"; collected = now_iso()
    try:
        manifest = SESSION.get("https://storage.googleapis.com/data.gdeltproject.org/gdeltv2/lastupdate.txt", timeout=15).text
        line = next(x for x in manifest.splitlines() if ".export.CSV.zip" in x)
        archive_url = line.split()[-1].replace("http://data.gdeltproject.org/", "https://storage.googleapis.com/data.gdeltproject.org/")
        archive = zipfile.ZipFile(io.BytesIO(SESSION.get(archive_url, timeout=25).content))
        text = archive.read(archive.namelist()[0]).decode("utf-8", errors="replace")
        labels = {"12":"Rejection","13":"Threat","14":"Protest","15":"Force posture","16":"Reduced relations","17":"Coercion","18":"Assault","19":"Armed conflict","20":"Mass violence"}
        candidates = []
        for raw in text.splitlines():
            c = raw.split("\t")
            if len(c) <= 60 or not c[56] or not c[57] or not c[60]: continue
            root = c[28]
            if int(root or 0) < 12: continue
            try: lat, lon = float(c[56]), float(c[57])
            except ValueError: continue
            candidates.append((int(c[33] or 0), c, lat, lon))
        candidates.sort(reverse=True, key=lambda x: x[0]); rows = []; seen = set()
        for article_count, c, lat, lon in candidates:
            key = (c[52], c[28])
            if key in seen: continue
            seen.add(key); root = c[28]; quad = int(c[29] or 0); sources = int(c[32] or 0); mentions = int(c[31] or 0)
            severity = "Critical" if quad == 4 and article_count >= 20 else "High" if quad >= 3 else "Watch"
            location = c[52] or "Unknown location"; event_type = labels.get(root, f"Event {root}")
            observed = c[59]
            if re.fullmatch(r"\d{8}T\d{6}Z", observed or ""):
                observed = datetime.strptime(observed, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC).isoformat()
            host = urlparse(c[60]).netloc.replace("www.", "") or "GDELT source"
            rows.append(_signal(id=f"gdelt-{c[0]}", source=provider, event_type=event_type,
                title=f"{event_type} signal near {location}", location=location, latitude=lat, longitude=lon,
                severity=severity, confidence=min(97, 65 + sources * 3),
                summary=f"GDELT identified a {event_type.lower()} signal referenced in {article_count} articles from {sources} sources with {mentions} mentions.",
                outlook="Corroborate the source report and monitor later GDELT updates for persistence or escalation.",
                source_url=c[60], source_name=host, observed_at=observed or collected, raw_json=json.dumps(c)))
            if len(rows) >= 20: break
        upsert_signals(rows); record_run(provider, "live", len(rows), "", collected); return rows
    except Exception as exc:
        record_run(provider, "unavailable", 0, str(exc), collected); return []


def _direct_url(raw):
    try:
        target = parse_qs(urlparse(raw).query).get("url", [None])[0]
        return unquote(target) if target else raw
    except Exception:
        return raw


def _article_summary(country, category, title, publisher, description):
    clean = re.sub(r"<[^>]+>", " ", html.unescape(description or "")); clean = " ".join(clean.split())
    if len(clean) > 80:
        return clean[:360].rsplit(" ", 1)[0] + ("…" if len(clean) > 360 else "")
    headline = re.sub(r"\s+-\s+[^-]+$", "", title).rstrip(".!?")
    return f"{publisher} reports that {headline[:1].lower() + headline[1:]}. This item is grouped under {category.lower()} for {country}; open the source for full context."


def _country_code(country):
    if country in COUNTRY_LOOKUP_ALIASES:
        return COUNTRY_LOOKUP_ALIASES[country]
    try:
        return pycountry.countries.lookup(country).alpha_2
    except LookupError:
        return "GB"


def _feed_articles(url, collected, coverage_scope, source_type="News reporting"):
    rows = []
    try:
        response = SESSION.get(url, timeout=(3.5, 7))
        response.raise_for_status()
        feed = feedparser.parse(response.content)
        for entry in feed.entries:
            publisher = entry.get("news_source") or entry.get("author") or entry.get("source", {}).get("title") or "News source"
            published = entry.get("published") or entry.get("updated") or collected
            try:
                published = parsedate_to_datetime(published).astimezone(UTC).isoformat()
            except Exception:
                try: published = datetime(*entry.published_parsed[:6], tzinfo=UTC).isoformat()
                except Exception: published = collected
            headline = entry.get("title", "Untitled report")
            if publisher == "News source" and " - " in headline:
                publisher = headline.rsplit(" - ", 1)[-1]
            source_meta = entry.get("source", {}) or {}
            source_home = source_meta.get("href") or source_meta.get("url") or entry.get("link", "")
            rows.append({"headline": headline, "publisher": publisher,
                "coverage_scope": coverage_scope, "url": _direct_url(entry.get("link", "")),
                "source_home": source_home, "published_at": published, "description": entry.get("summary", "")})
            rows[-1]["source_type"] = source_type
            rows[-1]["source_family"] = _source_family(publisher, source_home or rows[-1]["url"])
    except Exception:
        pass
    return rows


def _gdelt_story_articles(query, collected):
    rows = []
    try:
        response = SESSION.get("https://api.gdeltproject.org/api/v2/doc/doc", params={
            "query": query, "mode": "artlist", "maxrecords": 50, "format": "json",
            "sort": "datedesc", "timespan": "1month",
        }, timeout=(3.5, 8))
        response.raise_for_status()
        for article in response.json().get("articles", []):
            published = article.get("seendate") or collected
            if re.fullmatch(r"\d{8}T\d{6}Z", published or ""):
                published = datetime.strptime(published, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC).isoformat()
            article_url = article.get("url") or ""
            publisher = article.get("domain") or urlparse(article_url).netloc or "GDELT source"
            rows.append({"headline": article.get("title") or "Untitled report", "publisher": publisher,
                "coverage_scope": "International", "url": article_url, "source_home": article_url,
                "published_at": published, "description": "", "source_type": "GDELT-indexed news",
                "indexed_by": "GDELT",
                "source_family": _source_family(publisher, article_url)})
    except Exception:
        pass
    return rows


def _source_origin(publisher, source_url):
    name = (publisher or "").lower()
    for marker, country in SOURCE_COUNTRIES.items():
        if marker in name:
            return country
    host = urlparse(source_url or "").netloc.lower().split(":")[0]
    suffix = host.rsplit(".", 1)[-1].upper() if "." in host else ""
    suffix = "GB" if suffix == "UK" else suffix
    if len(suffix) == 2 and suffix.casefold() not in GENERIC_CCTLDS:
        match = pycountry.countries.get(alpha_2=suffix)
        if match:
            return match.name
    return "Origin unverified"


def _story_key(headline):
    return story_key(headline)


def _same_story(left, right):
    return same_story(left, right)


def _event_match(left_headline, right_headline):
    return event_match(left_headline, right_headline)


def _fact_variance(left_headline, right_headline):
    return fact_variance(left_headline, right_headline)


def _published_within(left, right, days=7):
    try:
        a = datetime.fromisoformat((left or "").replace("Z", "+00:00"))
        b = datetime.fromisoformat((right or "").replace("Z", "+00:00"))
        return abs((a - b).total_seconds()) <= days * 86400
    except (TypeError, ValueError):
        return True


def _same_event(left, right):
    """Require semantic event agreement and temporal proximity."""
    if not _published_within(left.get("published_at"), right.get("published_at")):
        return False
    return _event_match(left.get("headline", ""), right.get("headline", ""))


def _cluster_stories(items, country, category):
    clusters = []
    for item in sorted(items, key=lambda row: row.get("published_at") or "", reverse=True):
        key = _story_key(item["headline"])
        cluster = next((candidate for candidate in clusters if _same_event(item, candidate["items"][0])), None)
        if cluster:
            cluster["items"].append(item)
        else:
            clusters.append({"key": key, "items": [item]})
    stories = []
    for cluster in clusters:
        representative = cluster["items"][0].copy(); sources = []; seen = set()
        for item in cluster["items"]:
            source_key = item.get("source_family") or _source_family(
                item.get("publisher"), item.get("source_home") or item.get("url"))
            if source_key in seen:
                continue
            seen.add(source_key)
            origin = _source_origin(item["publisher"], item.get("source_home"))
            if origin == "Origin unverified" and item["coverage_scope"] == "Local / regional":
                origin = f"{country} (local-edition inference)"
            sources.append({"publisher": item["publisher"], "url": item["url"],
                "origin_country": origin,
                "coverage_scope": item["coverage_scope"],
                "source_type": item.get("source_type", "News reporting"),
                "source_family": item.get("source_family") or _source_family(item["publisher"], item.get("source_home") or item["url"]),
                "headline": item.get("headline", ""), "tone": tone_assessment(item.get("headline", ""))})
        representative["sources_json"] = json.dumps(sources, ensure_ascii=False)
        base_summary = _article_summary(country, category, representative["headline"],
            representative["publisher"], representative.pop("description", ""))
        representative["summary"] = f"{base_summary} This update was matched across {len(sources)} reporting source{'s' if len(sources) != 1 else ''}."
        scopes = {source["coverage_scope"] for source in sources}
        representative["coverage_scope"] = "Local + international" if len(scopes) > 1 else next(iter(scopes), "International")
        stories.append(representative)
    return stories


def _headline_searches(article, collected):
    key = _story_key(article["headline"])
    # Search the concrete event anchors, not an outlet's complete wording. This
    # retrieves independently phrased reports while avoiding generic topic hits.
    important = [word for word in key.split() if word not in {"live", "update", "news", "report"}]
    query = " ".join(important[:10])
    country = article.get("country") or ""
    if country and country.casefold() not in query.casefold():
        query = f'"{COUNTRY_ALIASES.get(country, country)}" {query}'
    if not query:
        return []
    encoded = quote_plus(query + " when:30d")
    searches = [
        ("feed", f"https://news.google.com/rss/search?q={encoded}&hl=en-GB&gl=GB&ceid=GB:en", "News reporting"),
        ("feed", f"https://www.bing.com/news/search?q={quote_plus(query)}&format=rss&setlang=en-GB&cc=GB", "News reporting"),
        ("gdelt", query, "News reporting"),
    ]
    lowered = f'{article.get("headline", "")} {article.get("summary", "")}'.casefold()
    if any(term in lowered for term in ("ship", "port", "vessel", "maritime", "canal", "strait", "tanker", "freight")):
        searches.append(("feed", _domain_news_search(query, OPERATIONAL_SOURCE_GROUPS["Maritime authority"]), "Maritime authority"))
        searches.append(("feed", _domain_news_search(query, OPERATIONAL_SOURCE_GROUPS["Carrier advisory"]), "Carrier advisory"))
        searches.append(("feed", _domain_news_search(query, OPERATIONAL_SOURCE_GROUPS["Maritime specialist"]), "Maritime specialist"))
    if any(term in lowered for term in ("airport", "airspace", "airline", "flight", "aviation", "air cargo", "airfreight")):
        searches.append(("feed", _domain_news_search(query, OPERATIONAL_SOURCE_GROUPS["Aviation authority"]), "Aviation authority"))
        searches.append(("feed", _domain_news_search(query, OPERATIONAL_SOURCE_GROUPS["Aviation specialist"]), "Aviation specialist"))
    logistics_domains = OPERATIONAL_SOURCE_GROUPS["Logistics specialist"]
    searches.append(("feed", _domain_news_search(query, logistics_domains), "Logistics corroboration"))
    return searches


def _domain_news_search(query, domains):
    domain_query = " OR ".join(f"site:{domain}" for domain in domains)
    search = f'({query}) ({domain_query}) when:30d'
    return f"https://news.google.com/rss/search?q={quote_plus(search)}&hl=en-GB&gl=GB&ceid=GB:en"


def _merge_story_sources(article, additions, country):
    try:
        sources = json.loads(article.get("sources_json") or "[]")
    except (TypeError, json.JSONDecodeError):
        sources = []
    seen_families = {
        source.get("source_family") or (source.get("publisher") or "").lower()
        for source in sources
    }
    for item in additions:
        if not _same_event(article, item):
            continue
        family = item.get("source_family") or _source_family(item.get("publisher"), item.get("source_home") or item.get("url"))
        if not family or family in seen_families:
            continue
        seen_families.add(family)
        origin = _source_origin(item.get("publisher"), item.get("source_home"))
        if origin == "Origin unverified" and item.get("coverage_scope") == "Local / regional":
            origin = f"{country} (local-edition inference)"
        variance = _fact_variance(article["headline"], item.get("headline", ""))
        sources.append({"publisher": item.get("publisher") or "News source", "url": item.get("url") or "",
            "origin_country": origin, "coverage_scope": item.get("coverage_scope", "International"),
            "source_type": item.get("source_type", "News reporting"),
            "indexed_by": item.get("indexed_by"),
            "source_family": item.get("source_family") or _source_family(item.get("publisher"), item.get("source_home") or item.get("url")),
            "headline": item.get("headline", ""), "tone": tone_assessment(item.get("headline", "")),
            "fact_variance": variance})
    article["sources_json"] = json.dumps(sources, ensure_ascii=False)
    scopes = {source.get("coverage_scope", "International") for source in sources}
    article["coverage_scope"] = "Local + international" if len(scopes) > 1 else next(iter(scopes), "International")
    base = re.sub(r"\s+This update was matched across \d+ reporting sources?\.$", "", article.get("summary") or "")
    article["summary"] = f"{base} This update was matched across {len(sources)} reporting source{'s' if len(sources) != 1 else ''}."
    return article


def _hazard_kinds(text):
    lowered = (text or "").casefold()
    return {kind for kind, markers in HAZARD_TERMS.items() if any(marker in lowered for marker in markers)}


def _attach_hazard_signals(article, country, signals):
    kinds = _hazard_kinds(f'{article.get("headline", "")} {article.get("summary", "")}')
    if not kinds:
        return article
    try:
        sources = json.loads(article.get("sources_json") or "[]")
    except (TypeError, json.JSONDecodeError):
        sources = []
    seen = {source.get("source_family") for source in sources}
    matched = []
    for signal in signals:
        place = f'{signal.get("country", "")} {signal.get("location", "")} {signal.get("title", "")}'.casefold()
        signal_kinds = _hazard_kinds(f'{signal.get("event_type", "")} {signal.get("title", "")} {signal.get("summary", "")}')
        if country.casefold() not in place or not (kinds & signal_kinds):
            continue
        family = signal.get("source")
        if family in seen:
            continue
        source_type = "Official hazard detection" if family in {"USGS", "GDACS"} else "Open-source event signal"
        sources.append({"publisher": family, "url": signal.get("source_url") or "",
                        "origin_country": "International / official", "coverage_scope": "Hazard signal",
                        "source_type": source_type, "source_family": family,
                        "headline": signal.get("title") or "Hazard signal",
                        "tone": tone_assessment(signal.get("title") or "")})
        seen.add(family); matched.append(family)
    article["sources_json"] = json.dumps(sources, ensure_ascii=False)
    article["hazard_status"] = "Officially detected and news corroborated" if matched else "News reported; no matching official detection stored"
    article["hazard_providers"] = matched
    return article


def collect_country(country, enrich=False):
    query_country = COUNTRY_ALIASES.get(country, country); country_code = _country_code(country)
    collected = now_iso(); output = {}; category_feeds = {}
    for category, terms in CATEGORIES.items():
        keywords = list(dict.fromkeys(word for term in terms for word in term.split()))
        category_query = " OR ".join(keywords)
        query = f'\"{query_country}\" ({category_query})'
        category_feeds[category] = [
            (f"https://news.google.com/rss/search?q={quote_plus(query + ' when:30d')}&hl=en&gl={country_code}&ceid={country_code}:en", "Local / regional"),
            (f"https://www.bing.com/news/search?q={quote_plus(query)}&format=rss&setlang=en-GB&cc=GB", "International"),
            (f"https://news.google.com/rss/search?q={quote_plus(query + ' when:30d')}&hl=en-GB&gl=GB&ceid=GB:en", "International"),
        ]
    hazard_query = (f'"{query_country}" (earthquake OR tsunami OR flood OR wildfire OR cyclone OR '
                    'hurricane OR typhoon OR volcano OR eruption OR drought OR landslide) when:30d')
    category_feeds["Environment and hazards"].extend([
        (f"https://news.google.com/rss/search?q={quote_plus(hazard_query)}&hl=en&gl={country_code}&ceid={country_code}:en", "Local / regional"),
        (f"https://news.google.com/rss/search?q={quote_plus(hazard_query)}&hl=en-GB&gl=GB&ceid=GB:en", "International"),
        (f"https://www.bing.com/news/search?q={quote_plus(hazard_query)}&format=rss&setlang=en-GB&cc=GB", "International"),
    ])
    merged_by_category = {category: [] for category in CATEGORIES}
    operational_feeds = []
    for source_type, domains in OPERATIONAL_SOURCE_GROUPS.items():
        domain_query = " OR ".join(f"site:{domain}" for domain in domains)
        query = f'"{query_country}" ({domain_query}) (disruption OR closure OR delay OR congestion OR strike OR advisory OR restriction) when:30d'
        operational_feeds.append((
            f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-GB&gl=GB&ceid=GB:en",
            "Primary operational", source_type,
        ))
    government_suffix = country_code.lower()
    government_query = f'"{query_country}" (site:gov.{government_suffix} OR site:gov) (transport OR port OR airport OR customs OR trade OR infrastructure) (closure OR disruption OR restriction OR advisory) when:30d'
    operational_feeds.append((
        f"https://news.google.com/rss/search?q={quote_plus(government_query)}&hl=en&gl={country_code}&ceid={country_code}:en",
        "Primary operational", "Government notice",
    ))
    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = {
            executor.submit(_feed_articles, url, collected, scope): category
            for category, feeds in category_feeds.items()
            for url, scope in feeds
        }
        for url, scope, source_type in operational_feeds:
            future = executor.submit(_feed_articles, url, collected, scope, source_type)
            futures[future] = "Infrastructure and supply chains"
        for future in as_completed(futures):
            merged_by_category[futures[future]].extend(future.result())

    for category in CATEGORIES:
        merged = merged_by_category[category]
        unique = {}; 
        for item in merged:
            unique.setdefault(((item["publisher"] or "").lower(), item["url"]), item)
        ranked = _cluster_stories(list(unique.values()), country, category)
        for article in ranked:
            relevance = country_supply_chain_relevance(article, country)
            article["country_relevance_score"] = relevance["score"]
            article["country_relevance_reason"] = relevance["reason"]
        ranked = [article for article in ranked if country_supply_chain_relevance(article, country)["relevant"]]
        ranked.sort(key=lambda a: a.get("published_at") or "", reverse=True)
        analyze_observation(country, category, ranked, len(unique), collected)
        operational = [item for item in ranked if item["coverage_scope"] == "Primary operational"]
        local = [item for item in ranked if item["coverage_scope"] == "Local / regional"]
        international = [item for item in ranked if item["coverage_scope"] == "International"]
        articles = []
        selected_urls = set()
        for pool, quota in ((operational, 2), (local, 1), (international, 2)):
            added = 0
            for item in pool:
                if item["url"] in selected_urls:
                    continue
                articles.append(item); selected_urls.add(item["url"]); added += 1
                if added == quota:
                    break
        for item in ranked:
            if len(articles) == 5:
                break
            if item["url"] not in selected_urls:
                articles.append(item); selected_urls.add(item["url"])
        articles = sorted(articles, key=lambda a: a.get("published_at") or "", reverse=True)[:5]
        for article in articles:
            article["country"] = country
        output[category] = articles

    enrichment = {(category, index): [] for category, articles in output.items() for index, _ in enumerate(articles)}
    if enrich:
        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = {}
            for category, articles in output.items():
                for index, article in enumerate(articles):
                    for search_type, value, source_type in _headline_searches(article, collected):
                        if search_type == "feed":
                            future = executor.submit(_feed_articles, value, collected, "International", source_type)
                        else:
                            future = executor.submit(_gdelt_story_articles, value, collected)
                        futures[future] = (category, index)
            for future in as_completed(futures):
                enrichment[futures[future]].extend(future.result())

    hazard_signals = recent_signals(200)
    for category, articles in output.items():
        for index, article in enumerate(articles):
            _merge_story_sources(article, enrichment[(category, index)], country)
            if category == "Environment and hazards":
                _attach_hazard_signals(article, country, hazard_signals)
        if articles:
            upsert_articles(country, category, articles, collected)
    total = sum(len(items) for items in output.values())
    record_country_refresh(country, total, collected)
    record_run("Global news", "live" if total else "unavailable", total, "", collected)
    return output


def collect_country_map_snapshot(country):
    """Lightweight country refresh used to update map risk before interaction.

    Unlike the full dialog enrichment, this makes four broad operational-news
    requests and stores up to five current stories. It is intentionally cheap
    enough to run concurrently for the countries already represented on the
    map or newly identified by global signals.
    """
    query_country = COUNTRY_ALIASES.get(country, country)
    country_code = _country_code(country)
    collected = now_iso()
    operational_terms = (
        "(port OR airport OR shipping OR freight OR cargo OR border OR railway OR factory OR power) "
        "(closure OR closed OR delay OR disruption OR strike OR attack OR war OR flood OR earthquake OR outage OR rerouting) when:7d"
    )
    query = f'"{query_country}" {operational_terms}'
    feeds = [
        (f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en&gl={country_code}&ceid={country_code}:en", "Local / regional", "News reporting"),
        (f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-GB&gl=GB&ceid=GB:en", "International", "News reporting"),
        (f"https://www.bing.com/news/search?q={quote_plus(query)}&format=rss&setlang=en-GB&cc=GB", "International", "News reporting"),
        (f"https://news.google.com/rss/search?q={quote_plus(query + ' (site:gov OR site:iata.org OR site:imo.org)')}&hl=en-GB&gl=GB&ceid=GB:en", "Primary operational", "Government notice"),
    ]
    rows = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(_feed_articles, url, collected, scope, source_type)
                   for url, scope, source_type in feeds]
        for future in as_completed(futures):
            try:
                rows.extend(future.result())
            except Exception:
                continue
    unique = {}
    for item in rows:
        unique.setdefault(((item.get("publisher") or "").casefold(), item.get("url") or ""), item)
    ranked = _cluster_stories(list(unique.values()), country, "Infrastructure and supply chains")
    accepted = []
    for article in ranked:
        relevance = country_supply_chain_relevance(article, country)
        article["country_relevance_score"] = relevance["score"]
        article["country_relevance_reason"] = relevance["reason"]
        if relevance["relevant"]:
            accepted.append(article)
    accepted.sort(key=lambda item: item.get("published_at") or "", reverse=True)
    accepted = accepted[:5]
    for article in accepted:
        _attach_hazard_signals(article, country, recent_signals(200))
    analyze_observation(country, "Infrastructure and supply chains", accepted, len(unique), collected)
    if accepted:
        upsert_articles(country, "Infrastructure and supply chains", accepted, collected)
    record_country_refresh(country, len(accepted), collected)
    return accepted


def collect_general_news(country, limit=10):
    """Return recent general-interest reporting for preference training.

    This feed is deliberately separate from country operational intelligence:
    it teaches the recommender what topics interest a profile, but its stories
    do not directly enter risk or confidence calculations.
    """
    query_country = COUNTRY_ALIASES.get(country, country)
    country_code = _country_code(country)
    collected = now_iso()
    query = f'"{query_country}" when:7d'
    feeds = [
        (f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en&gl={country_code}&ceid={country_code}:en", "Local / regional"),
        (f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-GB&gl=GB&ceid=GB:en", "International"),
        (f"https://www.bing.com/news/search?q={quote_plus(query_country)}&format=rss&setlang=en-GB&cc=GB", "International"),
    ]
    rows = []
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(_feed_articles, url, collected, scope) for url, scope in feeds]
        for future in as_completed(futures):
            try:
                rows.extend(future.result())
            except Exception:
                # One blocked or malformed public feed must not take down the
                # interest panel when the other feeds remain usable.
                continue
    unique = {}
    for item in rows:
        unique.setdefault(((item.get("publisher") or "").casefold(), item.get("url") or ""), item)
    clustered = _cluster_stories(list(unique.values()), country, "General news")
    related = []
    for item in clustered:
        # The upstream feeds are already constrained by an exact country
        # search. Do not require the country name to appear in the headline:
        # local publishers often use a city, institution or demonym instead.
        item["country"] = country
        classification = classify_general_news(item)
        item["general_categories"] = classification["labels"]
        item["general_category_scores"] = classification["scores"]
        item["general_category_evidence"] = classification["evidence"]
        # The category text is persisted with feedback and therefore becomes a
        # useful feature for the background preference model.
        item["category"] = " | ".join(classification["labels"])
        item["transport_mode"] = "General news"
        related.append(item)
    related.sort(key=lambda item: item.get("published_at") or "", reverse=True)
    return related[:limit]


def collect_all():
    return {"GDELT": collect_gdelt(), "GDACS": collect_gdacs(), "USGS": collect_usgs()}


AIS_MONITORING_ZONES = [
    [[-5, 95], [10, 110]], [[20, 50], [32, 62]], [[10, 38], [18, 48]],
    [[28, 30], [33, 34]], [[7, -82], [11, -77]], [[20, 116], [27, 124]],
    [[49, -6], [52, 3]], [[35, 25], [43, 31]], [[-37, 15], [-31, 22]],
    [[32, -121], [35, -117]],
]

AIS_TYPE_NAMES = {
    30: "Fishing", 31: "Towing", 32: "Towing", 33: "Dredging or underwater operations",
    34: "Diving operations", 35: "Military operations", 36: "Sailing", 37: "Pleasure craft",
    50: "Pilot vessel", 51: "Search and rescue", 52: "Tug", 53: "Port tender",
    55: "Law enforcement", 58: "Medical transport", 59: "Non-combatant ship",
}


def ais_vessel_type(value):
    """Return a readable AIS ship class; it is not the vessel's actual cargo manifest."""
    try:
        code = int(value)
    except (TypeError, ValueError):
        return str(value or "").strip()
    if 60 <= code <= 69:
        return "Passenger"
    if 70 <= code <= 79:
        return "Cargo"
    if 80 <= code <= 89:
        return "Tanker"
    if 90 <= code <= 99:
        return "Other"
    return AIS_TYPE_NAMES.get(code, f"AIS type {code}" if code else "")


def _format_ais_eta(value):
    if not value:
        return ""
    if isinstance(value, dict):
        month, day = value.get("Month"), value.get("Day")
        hour, minute = value.get("Hour"), value.get("Minute")
        if all(part is not None for part in (month, day, hour, minute)):
            return f"{int(day):02d}/{int(month):02d} {int(hour):02d}:{int(minute):02d} UTC"
    if isinstance(value, (int, float)):
        packed = int(value)
        month, day = (packed >> 16) & 15, (packed >> 11) & 31
        hour, minute = (packed >> 6) & 31, packed & 63
        if 1 <= month <= 12 and 1 <= day <= 31 and 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{day:02d}/{month:02d} {hour:02d}:{minute:02d} UTC"
    return str(value).strip()


def country_maritime_tracker_urls(country):
    """Country-centred browser alternatives when no embeddable open global AIS API is available."""
    try:
        south, north, west, east = _country_bounds(country, 0.5)
        latitude, longitude = (south + north) / 2, (west + east) / 2
        marine_traffic = (f"https://www.marinetraffic.com/en/ais/home/"
                          f"centerx:{longitude:.4f}/centery:{latitude:.4f}/zoom:5")
    except Exception:
        marine_traffic = "https://www.marinetraffic.com/en/ais/home"
    return {
        "MarineTraffic live map": marine_traffic,
        "VesselFinder live map": "https://www.vesselfinder.com/",
    }


def _country_bounds(country, margin=1.5):
    response = SESSION.get("https://nominatim.openstreetmap.org/search", params={
        "country": country, "format": "jsonv2", "limit": 1, "addressdetails": 0,
    }, headers={"User-Agent": "OSINTEarlyWarning-Docker/2.0 vessel-monitor"}, timeout=(3.5, 8))
    response.raise_for_status(); results = response.json()
    if not results or not results[0].get("boundingbox"):
        raise ValueError(f"No geographic bounds returned for {country}")
    south, north, west, east = map(float, results[0]["boundingbox"])
    return (max(-90, south-margin), min(90, north+margin), max(-180, west-margin), min(180, east+margin))


def _country_ais_zone(country):
    south,north,west,east=_country_bounds(country,1.5)
    return [[[south,west],[north,east]]]


def collect_aisstream(country=None, duration_seconds=None, max_positions=500):
    provider = "AISStream"; collected = now_iso()
    api_key = os.getenv("AISSTREAM_API_KEY", "").strip()
    duration = int(duration_seconds or os.getenv("AIS_CAPTURE_SECONDS", "15"))
    if not api_key:
        record_run(provider, "setup required", 0, "AISSTREAM_API_KEY is not configured", collected)
        return []
    rows = []
    rows_by_mmsi = {}
    static_by_mmsi = {}
    try:
        monitoring_country = country or "Global strategic waterways"
        monitoring_zones = _country_ais_zone(country) if country else AIS_MONITORING_ZONES
        ws = websocket.create_connection("wss://stream.aisstream.io/v0/stream", timeout=5)
        ws.send(json.dumps({"APIKey": api_key, "BoundingBoxes": monitoring_zones,
            "FilterMessageTypes": ["PositionReport", "StandardClassBPositionReport",
                                   "ExtendedClassBPositionReport", "ShipStaticData", "StaticDataReport"]}))
        ws.settimeout(1.5); deadline = datetime.now(UTC).timestamp() + max(2, min(duration, 30))
        while datetime.now(UTC).timestamp() < deadline and len(rows) < max_positions:
            try:
                payload = json.loads(ws.recv())
            except websocket.WebSocketTimeoutException:
                continue
            message = payload.get("Message") or {}
            message_type = payload.get("MessageType") or ""
            metadata = payload.get("MetaData") or payload.get("Metadata") or {}
            static = message.get("ShipStaticData") or message.get("StaticDataReport") or {}
            if message_type in {"ShipStaticData", "StaticDataReport"} or static:
                mmsi = str(metadata.get("MMSI") or static.get("UserID") or "").strip()
                if mmsi:
                    values = {
                        "ship_name": str(static.get("Name") or metadata.get("ShipName") or "").strip(),
                        "vessel_type": ais_vessel_type(static.get("Type") or metadata.get("ShipType")),
                        "imo": str(static.get("ImoNumber") or "").strip(),
                        "call_sign": str(static.get("CallSign") or "").strip(),
                        "destination": str(static.get("Destination") or "").strip(),
                        "eta": _format_ais_eta(static.get("Eta")),
                        "draught_m": static.get("MaximumPresentStaticDraught"),
                    }
                    static_by_mmsi[mmsi] = {key: value for key, value in values.items() if value not in (None, "")}
                    if mmsi in rows_by_mmsi:
                        rows_by_mmsi[mmsi].update(static_by_mmsi[mmsi])
                continue
            report = (message.get("PositionReport") or message.get("StandardClassBPositionReport")
                or message.get("ExtendedClassBPositionReport") or {})
            latitude = report.get("Latitude", metadata.get("latitude", metadata.get("Latitude")))
            longitude = report.get("Longitude", metadata.get("longitude", metadata.get("Longitude")))
            mmsi = str(metadata.get("MMSI") or report.get("UserID") or "").strip()
            if not mmsi or latitude is None or longitude is None:
                continue
            observed = metadata.get("time_utc") or now_iso()
            row = {"mmsi": mmsi, "ship_name": (metadata.get("ShipName") or "").strip() or f"MMSI {mmsi}",
                "vessel_type": ais_vessel_type(metadata.get("ShipType") or report.get("ShipType")),
                "imo": "", "call_sign": "", "last_port": "", "destination": "", "eta": "", "draught_m": None,
                "latitude": float(latitude), "longitude": float(longitude),
                "speed_knots": float(report.get("Sog") or 0), "course": float(report.get("Cog") or 0),
                "heading": float(report.get("TrueHeading") or 0), "observed_at": observed,
                "collected_at": collected, "source": provider, "monitor_country": monitoring_country}
            row.update(static_by_mmsi.get(mmsi, {}))
            rows.append(row); rows_by_mmsi[mmsi] = row
        ws.close()
        upsert_vessel_positions(rows)
        if rows:
            record_run(provider, "live", len(rows), "", collected)
        else:
            record_run(provider, "no data", 0,
                       "The AISStream connection opened but returned zero frames; the upstream beta feed may be silent.",
                       collected)
        return rows
    except Exception as exc:
        record_run(provider, "unavailable", 0, str(exc), collected); return []


def _fintraffic_items(payload):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get("features"), list):
        return payload["features"]
    for key in ("vessels", "locations", "data"):
        if isinstance(payload.get(key), list):
            return payload[key]
    return []


def collect_fintraffic_vessels(country, max_positions=500):
    """Official open live AIS fallback for Finland and nearby Finnish waters."""
    provider = "Fintraffic AIS"; collected = now_iso()
    if (country or "").strip().casefold() != "finland":
        return []
    try:
        locations_response = SESSION.get("https://meri.digitraffic.fi/api/ais/v1/locations", timeout=(4, 15))
        metadata_response = SESSION.get("https://meri.digitraffic.fi/api/ais/v1/vessels", timeout=(4, 15))
        locations_response.raise_for_status(); metadata_response.raise_for_status()
        metadata_by_mmsi = {}
        for item in _fintraffic_items(metadata_response.json()):
            values = item.get("properties", item) if isinstance(item, dict) else {}
            mmsi = str(values.get("mmsi") or values.get("MMSI") or values.get("id") or "").strip()
            if mmsi:
                metadata_by_mmsi[mmsi] = values
        rows = []
        for feature in _fintraffic_items(locations_response.json()):
            if not isinstance(feature, dict):
                continue
            props = feature.get("properties", feature)
            coordinates = (feature.get("geometry") or {}).get("coordinates") or []
            mmsi = str(feature.get("mmsi") or props.get("mmsi") or props.get("MMSI") or props.get("id") or "").strip()
            metadata = metadata_by_mmsi.get(mmsi, {})
            longitude = coordinates[0] if len(coordinates) >= 2 else props.get("longitude") or props.get("lon")
            latitude = coordinates[1] if len(coordinates) >= 2 else props.get("latitude") or props.get("lat")
            if not mmsi or latitude is None or longitude is None:
                continue
            raw_time = props.get("timestampExternal") or props.get("timestamp") or props.get("time") or collected
            try:
                numeric_time = float(raw_time)
                if numeric_time > 10_000_000_000:
                    numeric_time /= 1000
                observed = datetime.fromtimestamp(numeric_time, UTC).isoformat()
            except (TypeError, ValueError, OSError):
                observed = str(raw_time).replace("Z", "+00:00")
            draught = metadata.get("draught")
            if isinstance(draught, (int, float)):
                draught = float(draught) / 10
            rows.append({
                "mmsi": mmsi, "ship_name": str(metadata.get("name") or f"MMSI {mmsi}").strip(),
                "vessel_type": ais_vessel_type(metadata.get("shipType") or metadata.get("type")),
                "imo": str(metadata.get("imo") or "").strip(), "call_sign": str(metadata.get("callSign") or "").strip(),
                "last_port": "", "destination": str(metadata.get("destination") or "").strip(),
                "eta": _format_ais_eta(metadata.get("eta")), "draught_m": draught,
                "latitude": float(latitude), "longitude": float(longitude),
                "speed_knots": float(props.get("sog") or 0), "course": float(props.get("cog") or 0),
                "heading": float(props.get("heading") or 0), "observed_at": observed,
                "collected_at": collected, "source": provider, "monitor_country": country,
            })
            if len(rows) >= max_positions:
                break
        upsert_vessel_positions(rows)
        record_run(provider, "live" if rows else "no data", len(rows),
                   "" if rows else "Fintraffic returned no current vessel positions.", collected)
        return rows
    except Exception as exc:
        record_run(provider, "unavailable", 0, str(exc), collected)
        return []


def _country_is(country, *names):
    value = (country or "").strip().casefold()
    return value in {name.casefold() for name in names}


def collect_barentswatch_vessels(country, max_positions=500):
    """Open Norwegian Coastal Administration AIS, when free API credentials are configured."""
    provider = "BarentsWatch AIS"; collected = now_iso()
    if not _country_is(country, "Norway", "Svalbard and Jan Mayen"):
        return []
    client_id = os.getenv("BARENTSWATCH_CLIENT_ID", "").strip()
    client_secret = os.getenv("BARENTSWATCH_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        record_run(provider, "setup required", 0,
                   "Create a free BarentsWatch API client and configure BARENTSWATCH_CLIENT_ID and BARENTSWATCH_CLIENT_SECRET.",
                   collected)
        return []
    try:
        token_response = SESSION.post("https://id.barentswatch.no/connect/token", data={
            "client_id": client_id, "client_secret": client_secret,
            "scope": "ais", "grant_type": "client_credentials",
        }, timeout=(4, 12))
        token_response.raise_for_status()
        access_token = token_response.json().get("access_token")
        if not access_token:
            raise ValueError("BarentsWatch did not return an access token")
        response = SESSION.get(
            "https://live.ais.barentswatch.no/v1/latest/combined",
            params={"modelType": "Full"},
            headers={"Authorization": f"Bearer {access_token}"}, timeout=(4, 20),
        )
        response.raise_for_status(); rows = []
        for item in response.json() if isinstance(response.json(), list) else []:
            if not isinstance(item, dict) or item.get("latitude") is None or item.get("longitude") is None:
                continue
            mmsi = str(item.get("mmsi") or "").strip()
            if not mmsi:
                continue
            draught = item.get("draught")
            if isinstance(draught, (int, float)) and draught > 25:
                draught = float(draught) / 10
            rows.append({
                "mmsi": mmsi, "ship_name": str(item.get("name") or f"MMSI {mmsi}").strip(),
                "vessel_type": ais_vessel_type(item.get("shipType")),
                "imo": str(item.get("imoNumber") or "").strip(),
                "call_sign": str(item.get("callSign") or "").strip(), "last_port": "",
                "destination": str(item.get("destination") or "").strip(),
                "eta": _format_ais_eta(item.get("eta")), "draught_m": draught,
                "latitude": float(item["latitude"]), "longitude": float(item["longitude"]),
                "speed_knots": float(item.get("speedOverGround") or 0),
                "course": float(item.get("courseOverGround") or 0),
                "heading": float(item.get("trueHeading") or 0),
                "observed_at": str(item.get("msgtime") or collected).replace("Z", "+00:00"),
                "collected_at": collected, "source": provider, "monitor_country": country,
            })
            if len(rows) >= max_positions:
                break
        upsert_vessel_positions(rows)
        record_run(provider, "live" if rows else "no data", len(rows),
                   "" if rows else "BarentsWatch returned no current vessel positions.", collected)
        return rows
    except Exception as exc:
        record_run(provider, "unavailable", 0, str(exc), collected)
        return []


def _gfw_last_port(entry):
    """Extract a readable anchorage/port label across GFW event schema versions."""
    if not isinstance(entry, dict):
        return ""
    visit = entry.get("port_visit") or entry.get("portVisit") or {}
    regions = entry.get("regions") or {}
    candidates = [
        visit.get("name"), visit.get("portName"), entry.get("portName"),
        regions.get("namedAnchorage") if isinstance(regions, dict) else None,
        regions.get("port") if isinstance(regions, dict) else None,
    ]
    for value in candidates:
        if isinstance(value, dict):
            value = value.get("name") or value.get("label")
        if value:
            return str(value).strip()
    return ""


def enrich_vessels_with_gfw(vessels, max_lookups=12):
    """Add open GFW registry identity and recent port-visit context to live regional AIS rows."""
    provider = "Global Fishing Watch"; collected = now_iso()
    token = os.getenv("GFW_API_ACCESS_TOKEN", "").strip()
    if not vessels:
        return []
    if not token:
        record_run(provider, "setup required", 0,
                   "Configure a free non-commercial GFW_API_ACCESS_TOKEN for vessel identity and historical port visits.",
                   collected)
        return vessels
    enriched = []
    try:
        for original in vessels[:max_lookups]:
            row = dict(original); mmsi = str(row.get("mmsi") or "").strip()
            if not mmsi:
                enriched.append(row); continue
            search = SESSION.get("https://gateway.api.globalfishingwatch.org/v3/vessels/search", params={
                "query": mmsi, "datasets[0]": "public-global-vessel-identity:latest", "limit": 1,
            }, headers={"Authorization": f"Bearer {token}"}, timeout=(4, 12))
            search.raise_for_status(); entries = search.json().get("entries") or []
            if entries:
                identity = entries[0]
                vessel_id = identity.get("id") or identity.get("vesselId")
                self_reported = identity.get("selfReportedInfo") or []
                if isinstance(self_reported, list):
                    self_reported = self_reported[0] if self_reported else {}
                if isinstance(self_reported, dict):
                    row["ship_name"] = row.get("ship_name") or self_reported.get("shipname") or self_reported.get("shipName")
                    row["imo"] = row.get("imo") or self_reported.get("imo") or ""
                    row["call_sign"] = row.get("call_sign") or self_reported.get("callsign") or ""
                    row["vessel_type"] = row.get("vessel_type") or self_reported.get("shiptype") or ""
                if vessel_id:
                    events = SESSION.get("https://gateway.api.globalfishingwatch.org/v3/events", params={
                        "datasets[0]": "public-global-port-visits-events:latest",
                        "vessels[0]": vessel_id, "sort": "-start", "limit": 1, "offset": 0,
                    }, headers={"Authorization": f"Bearer {token}"}, timeout=(4, 12))
                    events.raise_for_status(); port_events = events.json().get("entries") or []
                    if port_events:
                        row["last_port"] = _gfw_last_port(port_events[0]) or row.get("last_port") or ""
                row["source"] = f'{row.get("source") or "AIS"} + Global Fishing Watch'
            enriched.append(row)
        enriched.extend(dict(item) for item in vessels[max_lookups:])
        upsert_vessel_positions(enriched)
        record_run(provider, "live", len(enriched), "Vessel identity/port-visit enrichment completed.", collected)
        return enriched
    except Exception as exc:
        record_run(provider, "unavailable", 0, str(exc), collected)
        return vessels


def collect_vessel_activity(country, duration_seconds=20, max_positions=500):
    """Hybrid open-data vessel pipeline: live official regional AIS plus GFW enrichment."""
    rows = []
    rows.extend(collect_fintraffic_vessels(country, max_positions=max_positions))
    rows.extend(collect_barentswatch_vessels(country, max_positions=max_positions))
    # Deduplicate by MMSI, preferring the record with more populated voyage fields.
    merged = {}
    for row in rows:
        key = str(row.get("mmsi") or "")
        current = merged.get(key)
        richness = sum(bool(row.get(field)) for field in
                       ("ship_name", "vessel_type", "imo", "call_sign", "last_port", "destination", "eta"))
        current_richness = sum(bool(current.get(field)) for field in
                               ("ship_name", "vessel_type", "imo", "call_sign", "last_port", "destination", "eta")) if current else -1
        if richness > current_richness:
            merged[key] = row
    return enrich_vessels_with_gfw(list(merged.values()))


def collect_opensky(country, max_aircraft=500):
    provider="OpenSky"; collected=now_iso()
    try:
        south,north,west,east=_country_bounds(country,1.0)
        headers={}; token=os.getenv("OPENSKY_TOKEN","").strip()
        if token: headers["Authorization"]=f"Bearer {token}"
        response=SESSION.get("https://opensky-network.org/api/states/all",params={
            "lamin":south,"lamax":north,"lomin":west,"lomax":east,"extended":1},
            headers=headers,timeout=(4,12))
        response.raise_for_status(); payload=response.json(); rows=[]
        observed=datetime.fromtimestamp(payload.get("time") or datetime.now(UTC).timestamp(),UTC).isoformat()
        for state in (payload.get("states") or [])[:max_aircraft]:
            if len(state)<17 or state[5] is None or state[6] is None: continue
            rows.append({"icao24":state[0],"callsign":str(state[1] or state[0]).strip(),
                "origin_country":state[2] or "Unknown","longitude":float(state[5]),"latitude":float(state[6]),
                "altitude_m":state[13] if state[13] is not None else state[7],
                "velocity_knots":float(state[9] or 0)*1.94384,"track_degrees":float(state[10] or 0),
                "vertical_rate":float(state[11] or 0),"on_ground":1 if state[8] else 0,
                "observed_at":observed,"collected_at":collected,"monitor_country":country,"source":provider})
        upsert_aircraft_states(rows);record_run(provider,"live",len(rows),"",collected);return rows
    except Exception as exc:
        record_run(provider,"unavailable",0,str(exc),collected);return []


def collect_flight_routes(aircraft, max_lookups=40, cache_hours=24):
    """Resolve ADS-B callsigns to static route metadata with a bounded cache."""
    callsigns = list(dict.fromkeys(
        str(item.get("callsign") or "").strip().upper() for item in aircraft
        if str(item.get("callsign") or "").strip()
    ))[:max_lookups]
    cached = flight_routes(callsigns)
    cutoff = datetime.now(UTC) - timedelta(hours=cache_hours)
    output = {}
    for callsign in callsigns:
        cached_route = cached.get(callsign)
        try:
            checked = datetime.fromisoformat(cached_route["checked_at"]).astimezone(UTC) if cached_route else None
        except (TypeError, ValueError):
            checked = None
        if checked and checked >= cutoff:
            output[callsign] = cached_route
            continue
        checked_at = now_iso()
        blank = {"callsign": callsign, "airline": None, "origin_name": None, "origin_country": None, "origin_iata": None,
                 "origin_icao": None, "origin_latitude": None, "origin_longitude": None,
                 "destination_name": None, "destination_country": None, "destination_iata": None, "destination_icao": None,
                 "destination_latitude": None, "destination_longitude": None,
                 "source": "ADSBDB", "status": "unavailable", "checked_at": checked_at}
        try:
            response = SESSION.get(f"https://api.adsbdb.com/v0/callsign/{quote_plus(callsign)}", timeout=(3, 6))
            response.raise_for_status()
            route = (response.json().get("response") or {}).get("flightroute") or {}
            origin, destination = route.get("origin") or {}, route.get("destination") or {}
            if not origin or not destination:
                raise ValueError("No route record")
            airline = route.get("airline") or {}
            blank.update({"airline": airline.get("name"), "origin_name": origin.get("name"),
                "origin_country": origin.get("country_name"),
                "origin_iata": origin.get("iata_code"), "origin_icao": origin.get("icao_code"),
                "origin_latitude": origin.get("latitude"), "origin_longitude": origin.get("longitude"),
                "destination_name": destination.get("name"), "destination_country": destination.get("country_name"),
                "destination_iata": destination.get("iata_code"),
                "destination_icao": destination.get("icao_code"), "destination_latitude": destination.get("latitude"),
                "destination_longitude": destination.get("longitude"), "status": "matched"})
        except Exception:
            pass
        upsert_flight_route(blank)
        output[callsign] = blank
    return output
