"""
db.py — SQLite wrapper
Thin helpers that mirror what the real pipeline's db.py does with Postgres,
but everything goes into a local giveback.db file instead.
No external dependencies, runs anywhere.
"""

import sqlite3
import json
from config import DB_PATH


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row   # so rows behave like dicts
    conn.execute("PRAGMA journal_mode=WAL")  # safe for concurrent reads
    return conn


def init_db():
    """
    Creates all tables on first run. Safe to call every startup —
    IF NOT EXISTS means it's a no-op after the first time.
    """
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS orgs (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                name                TEXT NOT NULL,
                phone               TEXT,
                city                TEXT,
                state               TEXT,
                full_address        TEXT,
                category            TEXT,
                rating              REAL,
                review_count        INTEGER DEFAULT 0,
                google_place_id     TEXT UNIQUE,
                google_maps_url     TEXT,
                photos              TEXT,           -- JSON array of photo URLs
                has_website         INTEGER DEFAULT 0,
                lead_tier           TEXT,           -- 'hot' | 'warm' | 'cold'
                pipeline_stage      TEXT DEFAULT 'scraped',
                -- builder outputs
                demo_html           TEXT,           -- full generated HTML (stored before deploy)
                demo_url            TEXT,           -- live github.io URL after deploy
                github_repo         TEXT,           -- repo name on GitHub
                demo_built_at       TEXT,
                -- outreach
                contact_email       TEXT,
                outreach_sent_at    TEXT,
                -- misc
                notes               TEXT,
                created_at          TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS scrape_runs (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                query               TEXT,
                city                TEXT,
                records_returned    INTEGER DEFAULT 0,
                new_records         INTEGER DEFAULT 0,
                dupes_skipped       INTEGER DEFAULT 0,
                status              TEXT,
                ran_at              TEXT DEFAULT (datetime('now'))
            );
        """)


def fetch_all(sql, params=()):
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def fetch_one(sql, params=()):
    with get_conn() as conn:
        row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


def execute(sql, params=()):
    with get_conn() as conn:
        conn.execute(sql, params)
        conn.commit()
