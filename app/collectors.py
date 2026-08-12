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

from app.database import record_country_refresh, record_run, upsert_aircraft_states, upsert_articles, upsert_signals, upsert_vessel_positions
from app.patterns import analyze_observation, event_match, fact_variance, same_story, story_key
from app.supply_chain import country_supply_chain_relevance, tone_assessment

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
                "published_at": published, "description": "", "source_type": "News reporting",
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
    if len(suffix) == 2:
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


def _cluster_stories(items, country, category):
    clusters = []
    for item in sorted(items, key=lambda row: row.get("published_at") or "", reverse=True):
        key = _story_key(item["headline"])
        cluster = next((candidate for candidate in clusters if _same_story(key, candidate["key"])), None)
        if cluster:
            cluster["items"].append(item)
        else:
            clusters.append({"key": key, "items": [item]})
    stories = []
    for cluster in clusters:
        representative = cluster["items"][0].copy(); sources = []; seen = set()
        for item in cluster["items"]:
            source_key = (item["publisher"] or "").lower()
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
    query = " ".join(key.split()[:14])
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
    story_key = _story_key(article["headline"])
    for item in additions:
        if not _event_match(article["headline"], item.get("headline", "")):
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
            "source_family": item.get("source_family") or _source_family(item.get("publisher"), item.get("source_home") or item.get("url")),
            "headline": item.get("headline", ""), "tone": tone_assessment(item.get("headline", "")),
            "fact_variance": variance})
    article["sources_json"] = json.dumps(sources, ensure_ascii=False)
    scopes = {source.get("coverage_scope", "International") for source in sources}
    article["coverage_scope"] = "Local + international" if len(scopes) > 1 else next(iter(scopes), "International")
    base = re.sub(r"\s+This update was matched across \d+ reporting sources?\.$", "", article.get("summary") or "")
    article["summary"] = f"{base} This update was matched across {len(sources)} reporting source{'s' if len(sources) != 1 else ''}."
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

    for category, articles in output.items():
        for index, article in enumerate(articles):
            _merge_story_sources(article, enrichment[(category, index)], country)
        if articles:
            upsert_articles(country, category, articles, collected)
    total = sum(len(items) for items in output.values())
    record_country_refresh(country, total, collected)
    record_run("Global news", "live" if total else "unavailable", total, "", collected)
    return output


def collect_all():
    return {"GDELT": collect_gdelt(), "GDACS": collect_gdacs(), "USGS": collect_usgs()}


AIS_MONITORING_ZONES = [
    [[-5, 95], [10, 110]], [[20, 50], [32, 62]], [[10, 38], [18, 48]],
    [[28, 30], [33, 34]], [[7, -82], [11, -77]], [[20, 116], [27, 124]],
    [[49, -6], [52, 3]], [[35, 25], [43, 31]], [[-37, 15], [-31, 22]],
    [[32, -121], [35, -117]],
]


def _country_bounds(country, margin=1.5):
    response = SESSION.get("https://nominatim.openstreetmap.org/search", params={
        "country": country, "format": "jsonv2", "limit": 1, "addressdetails": 0,
    }, headers={"User-Agent": "HorizonOSINT-Docker/1.0 vessel-monitor"}, timeout=(3.5, 8))
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
    try:
        monitoring_country = country or "Global strategic waterways"
        monitoring_zones = _country_ais_zone(country) if country else AIS_MONITORING_ZONES
        ws = websocket.create_connection("wss://stream.aisstream.io/v0/stream", timeout=5)
        ws.send(json.dumps({"APIKey": api_key, "BoundingBoxes": monitoring_zones,
            "FilterMessageTypes": ["PositionReport", "StandardClassBPositionReport", "ExtendedClassBPositionReport"]}))
        ws.settimeout(1.5); deadline = datetime.now(UTC).timestamp() + max(2, min(duration, 30))
        while datetime.now(UTC).timestamp() < deadline and len(rows) < max_positions:
            try:
                payload = json.loads(ws.recv())
            except websocket.WebSocketTimeoutException:
                continue
            message = payload.get("Message") or {}
            report = (message.get("PositionReport") or message.get("StandardClassBPositionReport")
                or message.get("ExtendedClassBPositionReport") or {})
            metadata = payload.get("MetaData") or {}
            latitude = report.get("Latitude", metadata.get("latitude", metadata.get("Latitude")))
            longitude = report.get("Longitude", metadata.get("longitude", metadata.get("Longitude")))
            mmsi = str(metadata.get("MMSI") or report.get("UserID") or "").strip()
            if not mmsi or latitude is None or longitude is None:
                continue
            observed = metadata.get("time_utc") or now_iso()
            rows.append({"mmsi": mmsi, "ship_name": (metadata.get("ShipName") or "").strip() or f"MMSI {mmsi}",
                "latitude": float(latitude), "longitude": float(longitude),
                "speed_knots": float(report.get("Sog") or 0), "course": float(report.get("Cog") or 0),
                "heading": float(report.get("TrueHeading") or 0), "observed_at": observed,
                "collected_at": collected, "source": provider, "monitor_country": monitoring_country})
        ws.close()
        upsert_vessel_positions(rows); record_run(provider, "live", len(rows), "", collected); return rows
    except Exception as exc:
        record_run(provider, "unavailable", 0, str(exc), collected); return []


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
