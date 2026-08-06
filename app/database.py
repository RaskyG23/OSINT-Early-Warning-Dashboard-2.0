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
          collected_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_signals_observed ON signals(observed_at DESC);
        CREATE INDEX IF NOT EXISTS idx_signals_source_severity ON signals(source, severity);
        CREATE INDEX IF NOT EXISTS idx_articles_country_category_date ON country_articles(country, category, published_at DESC);
        CREATE INDEX IF NOT EXISTS idx_runs_provider_date ON collection_runs(provider, collected_at DESC);
        PRAGMA optimize;
        """)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(country_articles)")}
        if "coverage_scope" not in columns:
            conn.execute("ALTER TABLE country_articles ADD COLUMN coverage_scope TEXT NOT NULL DEFAULT 'International'")


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
        conn.executemany("""INSERT INTO country_articles
          (country,category,headline,publisher,coverage_scope,url,summary,published_at,collected_at)
          VALUES (?,?,?,?,?,?,?,?,?)
          ON CONFLICT(country,category,url) DO UPDATE SET
          headline=excluded.headline,publisher=excluded.publisher,summary=excluded.summary,
          coverage_scope=excluded.coverage_scope,published_at=excluded.published_at,collected_at=excluded.collected_at""",
          [(country, category, a["headline"], a["publisher"], a.get("coverage_scope", "International"),
            a["url"], a["summary"], a.get("published_at"), collected_at) for a in articles])


def record_run(provider, status, count, message, collected_at):
    with connection() as conn:
        conn.execute("INSERT INTO collection_runs(provider,status,record_count,message,collected_at) VALUES(?,?,?,?,?)",
                     (provider, status, count, message, collected_at))


def recent_signals(limit=100):
    with connection() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM signals ORDER BY observed_at DESC LIMIT ?", (limit,))]


def country_articles(country, category, limit=5):
    with connection() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM country_articles WHERE country=? AND category=? ORDER BY published_at DESC LIMIT ?",
            (country, category, limit))]


def country_cache_fresh(country, max_age_minutes=10):
    with connection() as conn:
        row = conn.execute("SELECT collected_at FROM country_refreshes WHERE country=?", (country,)).fetchone()
    if not row or not row[0]:
        return False
    try:
        collected = datetime.fromisoformat(row[0]).astimezone(timezone.utc)
        return collected >= datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)
    except (TypeError, ValueError):
        return False


def record_country_refresh(country, article_count, collected_at):
    with connection() as conn:
        conn.execute("""INSERT INTO country_refreshes(country,article_count,collected_at) VALUES(?,?,?)
          ON CONFLICT(country) DO UPDATE SET article_count=excluded.article_count,collected_at=excluded.collected_at""",
          (country, article_count, collected_at))


def provider_status():
    with connection() as conn:
        rows = conn.execute("""SELECT r.* FROM collection_runs r JOIN
          (SELECT provider, MAX(id) id FROM collection_runs GROUP BY provider) x ON r.id=x.id""").fetchall()
    return {row["provider"]: dict(row) for row in rows}


def database_stats():
    with connection() as conn:
        signals = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        articles = conn.execute("SELECT COUNT(*) FROM country_articles").fetchone()[0]
        last = conn.execute("SELECT MAX(collected_at) FROM collection_runs").fetchone()[0]
    return {"signals": signals, "articles": articles, "last_collection": last}
