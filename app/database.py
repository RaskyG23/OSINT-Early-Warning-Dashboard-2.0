import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path(os.getenv("HORIZON_DB_PATH", "/app/data/horizon.sqlite"))


@contextmanager
def connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with connection() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS signals (
          id TEXT PRIMARY KEY,
          source TEXT NOT NULL,
          event_type TEXT NOT NULL,
          title TEXT NOT NULL,
          location TEXT,
          country TEXT,
          latitude REAL,
          longitude REAL,
          severity TEXT NOT NULL,
          confidence INTEGER NOT NULL,
          summary TEXT,
          outlook TEXT,
          source_url TEXT,
          source_name TEXT,
          observed_at TEXT NOT NULL,
          collected_at TEXT NOT NULL,
          raw_json TEXT
        );
        CREATE TABLE IF NOT EXISTS country_articles (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          country TEXT NOT NULL,
          category TEXT NOT NULL,
          headline TEXT NOT NULL,
          publisher TEXT,
          coverage_scope TEXT NOT NULL DEFAULT 'International',
          sources_json TEXT NOT NULL DEFAULT '[]',
          country_relevance_score INTEGER NOT NULL DEFAULT 0,
          country_relevance_reason TEXT,
          url TEXT NOT NULL,
          summary TEXT,
          published_at TEXT,
          collected_at TEXT NOT NULL,
          UNIQUE(country, category, url)
        );
        CREATE TABLE IF NOT EXISTS collection_runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          provider TEXT NOT NULL,
          status TEXT NOT NULL,
          record_count INTEGER NOT NULL DEFAULT 0,
          message TEXT,
          collected_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS country_refreshes (
          country TEXT PRIMARY KEY,
          article_count INTEGER NOT NULL DEFAULT 0,
          format_version INTEGER NOT NULL DEFAULT 3,
          collected_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS article_history (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          country TEXT NOT NULL,
          category TEXT NOT NULL,
          headline TEXT NOT NULL,
          publisher TEXT,
          url TEXT NOT NULL,
          story_key TEXT NOT NULL,
          sources_json TEXT NOT NULL DEFAULT '[]',
          summary TEXT,
          published_at TEXT,
          observed_at TEXT NOT NULL,
          UNIQUE(country, category, url, observed_at)
        );
        CREATE TABLE IF NOT EXISTS pattern_windows (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          country TEXT NOT NULL,
          category TEXT NOT NULL,
          candidate_count INTEGER NOT NULL,
          cluster_count INTEGER NOT NULL,
          collected_at TEXT NOT NULL,
          UNIQUE(country, category, collected_at)
        );
        CREATE TABLE IF NOT EXISTS story_patterns (
          pattern_id TEXT PRIMARY KEY,
          country TEXT NOT NULL,
          category TEXT NOT NULL,
          canonical_key TEXT NOT NULL,
          headline TEXT NOT NULL,
          first_seen TEXT NOT NULL,
          last_seen TEXT NOT NULL,
          observation_count INTEGER NOT NULL DEFAULT 1,
          active_windows INTEGER NOT NULL DEFAULT 1,
          source_count INTEGER NOT NULL DEFAULT 1,
          anomaly_score REAL NOT NULL DEFAULT 0,
          persistence_score REAL NOT NULL DEFAULT 0,
          status TEXT NOT NULL DEFAULT 'Emerging',
          entities_json TEXT NOT NULL DEFAULT '[]',
          routes_json TEXT NOT NULL DEFAULT '[]',
          transport_modes_json TEXT NOT NULL DEFAULT '[]',
          change_score REAL NOT NULL DEFAULT 0,
          change_status TEXT NOT NULL DEFAULT 'Baseline building',
          early_warning_score REAL NOT NULL DEFAULT 0,
          alert_level TEXT NOT NULL DEFAULT 'Informational',
          alert_confidence INTEGER NOT NULL DEFAULT 0,
          rationale_json TEXT NOT NULL DEFAULT '[]',
          sources_json TEXT NOT NULL DEFAULT '[]'
        );
        CREATE TABLE IF NOT EXISTS vessel_positions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          mmsi TEXT NOT NULL,
          ship_name TEXT,
          latitude REAL NOT NULL,
          longitude REAL NOT NULL,
          speed_knots REAL,
          course REAL,
          heading REAL,
          observed_at TEXT NOT NULL,
          collected_at TEXT NOT NULL,
          source TEXT NOT NULL DEFAULT 'AISStream',
          monitor_country TEXT NOT NULL DEFAULT 'Global strategic waterways',
          UNIQUE(mmsi, observed_at)
        );
        CREATE TABLE IF NOT EXISTS aircraft_states (
          id INTEGER PRIMARY KEY AUTOINCREMENT, icao24 TEXT NOT NULL, callsign TEXT,
          origin_country TEXT, latitude REAL NOT NULL, longitude REAL NOT NULL,
          altitude_m REAL, velocity_knots REAL, track_degrees REAL, vertical_rate REAL,
          on_ground INTEGER NOT NULL DEFAULT 0, observed_at TEXT NOT NULL,
          collected_at TEXT NOT NULL, monitor_country TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'OpenSky',
          UNIQUE(icao24, observed_at)
        );
        CREATE INDEX IF NOT EXISTS idx_signals_observed ON signals(observed_at DESC);
        CREATE INDEX IF NOT EXISTS idx_signals_source_severity ON signals(source, severity);
        CREATE INDEX IF NOT EXISTS idx_articles_country_category_date ON country_articles(country, category, published_at DESC);
        CREATE INDEX IF NOT EXISTS idx_runs_provider_date ON collection_runs(provider, collected_at DESC);
        CREATE INDEX IF NOT EXISTS idx_history_country_category_time ON article_history(country, category, observed_at DESC);
        CREATE INDEX IF NOT EXISTS idx_pattern_windows_lookup ON pattern_windows(country, category, collected_at DESC);
        CREATE INDEX IF NOT EXISTS idx_story_patterns_country_time ON story_patterns(country, last_seen DESC);
        CREATE INDEX IF NOT EXISTS idx_vessel_mmsi_time ON vessel_positions(mmsi, observed_at DESC);
        CREATE INDEX IF NOT EXISTS idx_vessel_time ON vessel_positions(observed_at DESC);
        CREATE INDEX IF NOT EXISTS idx_aircraft_country_time ON aircraft_states(monitor_country, observed_at DESC);
        PRAGMA optimize;
        """)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(country_articles)")}
        if "coverage_scope" not in columns:
            conn.execute("ALTER TABLE country_articles ADD COLUMN coverage_scope TEXT NOT NULL DEFAULT 'International'")
        if "sources_json" not in columns:
            conn.execute("ALTER TABLE country_articles ADD COLUMN sources_json TEXT NOT NULL DEFAULT '[]'")
        if "country_relevance_score" not in columns:
            conn.execute("ALTER TABLE country_articles ADD COLUMN country_relevance_score INTEGER NOT NULL DEFAULT 0")
        if "country_relevance_reason" not in columns:
            conn.execute("ALTER TABLE country_articles ADD COLUMN country_relevance_reason TEXT")
        refresh_columns = {row[1] for row in conn.execute("PRAGMA table_info(country_refreshes)")}
        if "format_version" not in refresh_columns:
            conn.execute("ALTER TABLE country_refreshes ADD COLUMN format_version INTEGER NOT NULL DEFAULT 1")
        history_columns = {row[1] for row in conn.execute("PRAGMA table_info(article_history)")}
        if "summary" not in history_columns:
            conn.execute("ALTER TABLE article_history ADD COLUMN summary TEXT")
        vessel_columns = {row[1] for row in conn.execute("PRAGMA table_info(vessel_positions)")}
        if "monitor_country" not in vessel_columns:
            conn.execute("ALTER TABLE vessel_positions ADD COLUMN monitor_country TEXT NOT NULL DEFAULT 'Global strategic waterways'")
        pattern_columns = {row[1] for row in conn.execute("PRAGMA table_info(story_patterns)")}
        pattern_additions = {
            "entities_json": "TEXT NOT NULL DEFAULT '[]'",
            "routes_json": "TEXT NOT NULL DEFAULT '[]'",
            "transport_modes_json": "TEXT NOT NULL DEFAULT '[]'",
            "change_score": "REAL NOT NULL DEFAULT 0",
            "change_status": "TEXT NOT NULL DEFAULT 'Baseline building'",
            "early_warning_score": "REAL NOT NULL DEFAULT 0",
            "alert_level": "TEXT NOT NULL DEFAULT 'Informational'",
            "alert_confidence": "INTEGER NOT NULL DEFAULT 0",
            "rationale_json": "TEXT NOT NULL DEFAULT '[]'",
        }
        for column, definition in pattern_additions.items():
            if column not in pattern_columns:
                conn.execute(f"ALTER TABLE story_patterns ADD COLUMN {column} {definition}")


def upsert_signals(records):
    if not records:
        return
    sql = """INSERT INTO signals
      (id,source,event_type,title,location,country,latitude,longitude,severity,confidence,summary,outlook,source_url,source_name,observed_at,collected_at,raw_json)
      VALUES (:id,:source,:event_type,:title,:location,:country,:latitude,:longitude,:severity,:confidence,:summary,:outlook,:source_url,:source_name,:observed_at,:collected_at,:raw_json)
      ON CONFLICT(id) DO UPDATE SET
      title=excluded.title, location=excluded.location, country=excluded.country,
      latitude=excluded.latitude, longitude=excluded.longitude, severity=excluded.severity,
      confidence=excluded.confidence, summary=excluded.summary, outlook=excluded.outlook,
      source_url=excluded.source_url, source_name=excluded.source_name,
      observed_at=excluded.observed_at, collected_at=excluded.collected_at, raw_json=excluded.raw_json"""
    with connection() as conn:
        conn.executemany(sql, records)


def upsert_articles(country, category, articles, collected_at):
    with connection() as conn:
        conn.execute("DELETE FROM country_articles WHERE country=? AND category=?", (country, category))
        conn.executemany("""INSERT INTO country_articles
          (country,category,headline,publisher,coverage_scope,sources_json,country_relevance_score,country_relevance_reason,url,summary,published_at,collected_at)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(country,category,url) DO UPDATE SET
          headline=excluded.headline,publisher=excluded.publisher,summary=excluded.summary,
          coverage_scope=excluded.coverage_scope,sources_json=excluded.sources_json,
          country_relevance_score=excluded.country_relevance_score,country_relevance_reason=excluded.country_relevance_reason,
          published_at=excluded.published_at,collected_at=excluded.collected_at""",
          [(country, category, a["headline"], a["publisher"], a.get("coverage_scope", "International"),
            a.get("sources_json", "[]"), a.get("country_relevance_score", 0), a.get("country_relevance_reason", ""),
            a["url"], a["summary"], a.get("published_at"), collected_at) for a in articles])


def record_run(provider, status, count, message, collected_at):
    with connection() as conn:
        conn.execute("INSERT INTO collection_runs(provider,status,record_count,message,collected_at) VALUES(?,?,?,?,?)",
                     (provider, status, count, message, collected_at))


def upsert_vessel_positions(records):
    if not records:
        return
    with connection() as conn:
        conn.executemany("""INSERT INTO vessel_positions
          (mmsi,ship_name,latitude,longitude,speed_knots,course,heading,observed_at,collected_at,source,monitor_country)
          VALUES (:mmsi,:ship_name,:latitude,:longitude,:speed_knots,:course,:heading,:observed_at,:collected_at,:source,:monitor_country)
          ON CONFLICT(mmsi,observed_at) DO UPDATE SET ship_name=excluded.ship_name,
          latitude=excluded.latitude,longitude=excluded.longitude,speed_knots=excluded.speed_knots,
          course=excluded.course,heading=excluded.heading,collected_at=excluded.collected_at,
          monitor_country=excluded.monitor_country""", records)
        conn.execute("""DELETE FROM vessel_positions WHERE id NOT IN
          (SELECT id FROM vessel_positions ORDER BY observed_at DESC LIMIT 10000)""")


def latest_vessels(limit=500, country=None):
    with connection() as conn:
        query = """WITH latest AS (
          SELECT vessel_positions.*, ROW_NUMBER() OVER (PARTITION BY mmsi ORDER BY observed_at DESC) AS vessel_rank
          FROM vessel_positions
          WHERE (? IS NULL OR monitor_country = ?)
        ) SELECT * FROM latest WHERE vessel_rank=1 ORDER BY observed_at DESC LIMIT ?"""
        return [dict(row) for row in conn.execute(query, (country, country, limit))]


def vessel_history(mmsi, limit=100):
    with connection() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM vessel_positions WHERE mmsi=? ORDER BY observed_at DESC LIMIT ?", (str(mmsi), limit))]


def upsert_aircraft_states(records):
    if not records: return
    with connection() as conn:
        conn.executemany("""INSERT INTO aircraft_states
          (icao24,callsign,origin_country,latitude,longitude,altitude_m,velocity_knots,track_degrees,
           vertical_rate,on_ground,observed_at,collected_at,monitor_country,source)
          VALUES (:icao24,:callsign,:origin_country,:latitude,:longitude,:altitude_m,:velocity_knots,
          :track_degrees,:vertical_rate,:on_ground,:observed_at,:collected_at,:monitor_country,:source)
          ON CONFLICT(icao24,observed_at) DO UPDATE SET callsign=excluded.callsign,latitude=excluded.latitude,
          longitude=excluded.longitude,altitude_m=excluded.altitude_m,velocity_knots=excluded.velocity_knots,
          track_degrees=excluded.track_degrees,vertical_rate=excluded.vertical_rate,on_ground=excluded.on_ground,
          monitor_country=excluded.monitor_country,collected_at=excluded.collected_at""",records)
        conn.execute("""DELETE FROM aircraft_states WHERE id NOT IN
          (SELECT id FROM aircraft_states ORDER BY observed_at DESC LIMIT 10000)""")


def latest_aircraft(country, limit=500):
    with connection() as conn:
        return [dict(row) for row in conn.execute("""WITH latest AS (
          SELECT aircraft_states.*,ROW_NUMBER() OVER(PARTITION BY icao24 ORDER BY observed_at DESC) aircraft_rank
          FROM aircraft_states WHERE monitor_country=?
        ) SELECT * FROM latest WHERE aircraft_rank=1 ORDER BY observed_at DESC LIMIT ?""",(country,limit))]


def aircraft_history(icao24, limit=100):
    with connection() as conn:
        return [dict(row) for row in conn.execute(
          "SELECT * FROM aircraft_states WHERE icao24=? ORDER BY observed_at DESC LIMIT ?",(icao24,limit))]


def recent_signals(limit_per_source=100):
    with connection() as conn:
        return [dict(row) for row in conn.execute("""WITH ranked AS (
          SELECT signals.*, ROW_NUMBER() OVER (PARTITION BY source ORDER BY observed_at DESC) AS source_rank
          FROM signals
        )
        SELECT * FROM ranked WHERE source_rank <= ? ORDER BY observed_at DESC""", (limit_per_source,))]


def country_articles(country, category, limit=5):
    with connection() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM country_articles WHERE country=? AND category=? ORDER BY published_at DESC LIMIT ?",
            (country, category, limit))]


def recent_country_articles(limit=750):
    with connection() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM country_articles ORDER BY published_at DESC LIMIT ?", (limit,))]


def all_country_articles(limit=5000):
    with connection() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM country_articles ORDER BY published_at DESC LIMIT ?", (limit,))]


def country_cache_fresh(country, max_age_minutes=10):
    with connection() as conn:
        row = conn.execute("SELECT collected_at,format_version FROM country_refreshes WHERE country=?", (country,)).fetchone()
    if not row or not row[0] or row[1] < 3:
        return False
    try:
        collected = datetime.fromisoformat(row[0]).astimezone(timezone.utc)
        return collected >= datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)
    except (TypeError, ValueError):
        return False


def record_country_refresh(country, article_count, collected_at):
    with connection() as conn:
        conn.execute("""INSERT INTO country_refreshes(country,article_count,format_version,collected_at) VALUES(?,?,3,?)
          ON CONFLICT(country) DO UPDATE SET article_count=excluded.article_count,
          format_version=excluded.format_version,collected_at=excluded.collected_at""",
          (country, article_count, collected_at))


def record_article_history(country, category, articles, observed_at, key_function):
    if not articles:
        return
    with connection() as conn:
        conn.executemany("""INSERT OR IGNORE INTO article_history
          (country,category,headline,publisher,url,story_key,sources_json,summary,published_at,observed_at)
          VALUES (?,?,?,?,?,?,?,?,?,?)""", [
            (country, category, article["headline"], article.get("publisher"), article["url"],
             key_function(article["headline"]), article.get("sources_json", "[]"),
             article.get("summary"), article.get("published_at"), observed_at)
            for article in articles
        ])


def country_article_history(country, limit=10):
    """Return the latest distinct persisted stories for a country."""
    with connection() as conn:
        return [dict(row) for row in conn.execute(
            """SELECT h.* FROM article_history h
               JOIN (
                 SELECT story_key, MAX(observed_at) AS latest_observation
                 FROM article_history WHERE country=? GROUP BY story_key
               ) latest ON latest.story_key=h.story_key AND latest.latest_observation=h.observed_at
               WHERE h.country=?
               GROUP BY h.story_key
               ORDER BY datetime(COALESCE(h.published_at,h.observed_at)) DESC, h.observed_at DESC LIMIT ?""",
            (country, country, limit),
        )]


def pattern_baseline(country, category, limit=30):
    with connection() as conn:
        return [row[0] for row in conn.execute(
            """SELECT cluster_count FROM pattern_windows
               WHERE country=? AND category=? ORDER BY collected_at DESC LIMIT ?""",
            (country, category, limit),
        )]


def record_pattern_window(country, category, candidate_count, cluster_count, collected_at):
    with connection() as conn:
        conn.execute("""INSERT OR IGNORE INTO pattern_windows
          (country,category,candidate_count,cluster_count,collected_at) VALUES(?,?,?,?,?)""",
          (country, category, candidate_count, cluster_count, collected_at))


def pattern_candidates(country, category, since):
    with connection() as conn:
        return [dict(row) for row in conn.execute(
            """SELECT * FROM story_patterns WHERE country=? AND category=? AND last_seen>=?
               ORDER BY last_seen DESC""", (country, category, since))]


def upsert_story_pattern(pattern):
    with connection() as conn:
        conn.execute("""INSERT INTO story_patterns
          (pattern_id,country,category,canonical_key,headline,first_seen,last_seen,
           observation_count,active_windows,source_count,anomaly_score,persistence_score,status,
           entities_json,routes_json,transport_modes_json,change_score,change_status,
           early_warning_score,alert_level,alert_confidence,rationale_json,sources_json)
          VALUES (:pattern_id,:country,:category,:canonical_key,:headline,:first_seen,:last_seen,
           :observation_count,:active_windows,:source_count,:anomaly_score,:persistence_score,:status,
           :entities_json,:routes_json,:transport_modes_json,:change_score,:change_status,
           :early_warning_score,:alert_level,:alert_confidence,:rationale_json,:sources_json)
          ON CONFLICT(pattern_id) DO UPDATE SET
           headline=excluded.headline,last_seen=excluded.last_seen,
           observation_count=excluded.observation_count,active_windows=excluded.active_windows,
           source_count=excluded.source_count,anomaly_score=excluded.anomaly_score,
           persistence_score=excluded.persistence_score,status=excluded.status,
           entities_json=excluded.entities_json,routes_json=excluded.routes_json,
           transport_modes_json=excluded.transport_modes_json,change_score=excluded.change_score,
           change_status=excluded.change_status,early_warning_score=excluded.early_warning_score,
           alert_level=excluded.alert_level,alert_confidence=excluded.alert_confidence,
           rationale_json=excluded.rationale_json,
           sources_json=excluded.sources_json""", pattern)


def country_patterns(country, limit=12, max_age_days=30):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
    with connection() as conn:
        return [dict(row) for row in conn.execute(
            """SELECT * FROM story_patterns WHERE country=? AND last_seen>=?
               ORDER BY early_warning_score DESC,anomaly_score DESC,persistence_score DESC,last_seen DESC LIMIT ?""",
            (country, cutoff, limit))]


def country_alerts(country, limit=10, max_age_days=30):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
    with connection() as conn:
        return [dict(row) for row in conn.execute(
            """SELECT * FROM story_patterns WHERE country=? AND last_seen>=? AND early_warning_score>=25
               ORDER BY early_warning_score DESC,alert_confidence DESC,last_seen DESC LIMIT ?""",
            (country, cutoff, limit))]


def active_pattern_count(max_age_days=30):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
    with connection() as conn:
        return conn.execute("SELECT COUNT(*) FROM story_patterns WHERE last_seen>=?", (cutoff,)).fetchone()[0]


def active_alert_count(max_age_days=30, minimum_score=45):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
    with connection() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM story_patterns WHERE last_seen>=? AND early_warning_score>=?",
            (cutoff, minimum_score),
        ).fetchone()[0]


def provider_status():
    with connection() as conn:
        rows = conn.execute("""SELECT r.* FROM collection_runs r JOIN
          (SELECT provider, MAX(id) id FROM collection_runs GROUP BY provider) x ON r.id=x.id""").fetchall()
    return {row["provider"]: dict(row) for row in rows}


def database_stats():
    with connection() as conn:
        signals = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        articles = conn.execute("SELECT COUNT(*) FROM country_articles").fetchone()[0]
        patterns = conn.execute("SELECT COUNT(*) FROM story_patterns").fetchone()[0]
        last = conn.execute("SELECT MAX(collected_at) FROM collection_runs").fetchone()[0]
    return {"signals": signals, "articles": articles, "patterns": patterns, "last_collection": last}
