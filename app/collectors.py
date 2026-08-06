import hashlib
import html
import io
import json
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import feedparser
import pycountry
import requests

from app.database import record_country_refresh, record_run, upsert_articles, upsert_signals

UTC = timezone.utc
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "HorizonOSINT-Docker/1.0"})

CATEGORIES = {
    "Conflicts and war": ["war conflict military", "attack security ceasefire", "defence armed forces"],
    "Politics and governance": ["politics government", "election parliament", "president minister policy"],
    "Economy and markets": ["economy markets", "inflation trade business", "investment sanctions finance"],
    "Environment and hazards": ["environment climate", "flood earthquake wildfire", "storm drought disaster"],
    "Infrastructure and supply chains": ["infrastructure energy", "transport port shipping", "power supply chain"],
}

COUNTRY_ALIASES = {
    "United States of America": "US", "United States": "US", "United Kingdom": "UK",
    "Russian Federation": "Russia", "United Arab Emirates": "UAE",
    "Democratic Republic of the Congo": "DR Congo", "Côte d'Ivoire": "Ivory Coast",
}

COUNTRY_LOOKUP_ALIASES = {
    "Bolivia": "BO", "Brunei": "BN", "Democratic Republic of the Congo": "CD",
    "Iran": "IR", "Laos": "LA", "Moldova": "MD", "North Korea": "KP",
    "Palestine": "PS", "Russia": "RU", "South Korea": "KR", "Syria": "SY",
    "Taiwan": "TW", "Tanzania": "TZ", "Venezuela": "VE", "Vietnam": "VN",
}


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
    try:
        end = datetime.now(UTC).date(); start = end - timedelta(days=7)
        url = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
        params = {"eventlist":"EQ;TC;FL;VO;DR;WF","fromdate":start.isoformat(),"todate":end.isoformat(),"alertlevel":"red;orange;green","pagesize":50}
        data = SESSION.get(url, params=params, timeout=20).json(); rows = []
        labels = {"EQ":"Earthquake","TC":"Tropical cyclone","FL":"Flood","VO":"Volcano","DR":"Drought","WF":"Wildfire"}
        for item in data.get("features", [])[:30]:
            if item.get("geometry", {}).get("type") != "Point": continue
            p = item["properties"]; lon, lat = item["geometry"]["coordinates"][:2]
            alert = str(p.get("alertlevel", "green")).lower(); severity = "Critical" if alert == "red" else "High" if alert == "orange" else "Watch"
            report = (p.get("url") or {}).get("report") or "https://www.gdacs.org/"
            description = re.sub(r"<[^>]+>", " ", p.get("htmldescription") or p.get("description") or "")
            rows.append(_signal(id=f"gdacs-{p.get('eventtype')}-{p.get('eventid')}", source=provider,
                event_type=labels.get(p.get("eventtype"), p.get("eventtype", "Hazard")), title=p.get("name") or p.get("description") or "GDACS alert",
                location=p.get("country") or "Global", latitude=lat, longitude=lon, severity=severity, confidence=95,
                summary=f"GDACS {alert} alert: {' '.join(description.split())}", outlook="Review the official GDACS report for affected areas and exposure estimates.",
                source_url=report, source_name="GDACS", observed_at=p.get("datemodified") or collected, raw_json=json.dumps(item)))
        upsert_signals(rows); record_run(provider, "live", len(rows), "", collected); return rows
    except Exception as exc:
        record_run(provider, "unavailable", 0, str(exc), collected); return []


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


def _feed_articles(url, collected, coverage_scope):
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
            rows.append({"headline": headline, "publisher": publisher,
                "coverage_scope": coverage_scope, "url": _direct_url(entry.get("link", "")),
                "published_at": published, "description": entry.get("summary", "")})
    except Exception:
        pass
    return rows


def collect_country(country):
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
    merged_by_category = {category: [] for category in CATEGORIES}
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(_feed_articles, url, collected, scope): category
            for category, feeds in category_feeds.items()
            for url, scope in feeds
        }
        for future in as_completed(futures):
            merged_by_category[futures[future]].extend(future.result())

    for category in CATEGORIES:
        merged = merged_by_category[category]
        unique = {}; 
        for item in merged:
            key = re.sub(r"\s+-\s+[^-]+$", "", item["headline"].lower())
            unique.setdefault((item["coverage_scope"], key), item)
        ranked = sorted(unique.values(), key=lambda a: a.get("published_at") or "", reverse=True)
        local = [item for item in ranked if item["coverage_scope"] == "Local / regional"]
        international = [item for item in ranked if item["coverage_scope"] == "International"]
        articles = []
        selected_urls = set()
        for pool, quota in ((local, 2), (international, 3)):
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
        for item in articles:
            item["summary"] = _article_summary(country, category, item["headline"], item["publisher"], item.pop("description", ""))
        upsert_articles(country, category, articles, collected); output[category] = articles
    total = sum(len(items) for items in output.values())
    record_country_refresh(country, total, collected)
    record_run("Global news", "live" if total else "unavailable", total, "", collected)
    return output


def collect_all():
    return {"GDELT": collect_gdelt(), "GDACS": collect_gdacs(), "USGS": collect_usgs()}
