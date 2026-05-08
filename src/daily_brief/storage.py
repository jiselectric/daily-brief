from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import DATA_DIR

DB_PATH = DATA_DIR / "brief.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_articles (
    fingerprint TEXT PRIMARY KEY,
    source_id   TEXT NOT NULL,
    title       TEXT NOT NULL,
    url         TEXT NOT NULL,
    first_seen  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_seen_first_seen ON seen_articles(first_seen);

-- Reserved for v2 feedback loop.
CREATE TABLE IF NOT EXISTS feedback (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id  TEXT NOT NULL,
    headline    TEXT NOT NULL,
    topic       TEXT,
    rating      INTEGER NOT NULL,    -- +1 / -1
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT NOT NULL,
    completed_at    TEXT,
    article_count   INTEGER,
    cluster_count   INTEGER,
    delivered       INTEGER DEFAULT 0,
    error           TEXT
);
"""


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


def already_seen(fingerprint: str) -> bool:
    with connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM seen_articles WHERE fingerprint = ?",
            (fingerprint,),
        ).fetchone()
        return row is not None


def mark_seen_batch(rows: list[tuple[str, str, str, str]]) -> None:
    """rows: [(fingerprint, source_id, title, url)]"""
    if not rows:
        return
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO seen_articles (fingerprint, source_id, title, url, first_seen) "
            "VALUES (?, ?, ?, ?, ?)",
            [(*r, now) for r in rows],
        )


def prune_seen(days: int = 30) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with connect() as conn:
        cur = conn.execute("DELETE FROM seen_articles WHERE first_seen < ?", (cutoff,))
        return cur.rowcount


def start_run() -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO runs (started_at) VALUES (?)",
            (datetime.now(timezone.utc).isoformat(),),
        )
        return cur.lastrowid


def finish_run(
    run_id: int,
    article_count: int,
    cluster_count: int,
    delivered: bool,
    error: str | None = None,
) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE runs SET completed_at = ?, article_count = ?, cluster_count = ?, "
            "delivered = ?, error = ? WHERE id = ?",
            (
                datetime.now(timezone.utc).isoformat(),
                article_count,
                cluster_count,
                1 if delivered else 0,
                error,
                run_id,
            ),
        )
