"""Shared SQLite state for pollers — last-seen ids, payload hashes, etc."""
from __future__ import annotations
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(os.environ.get("ODDS_POLLER_DB",
                              r"C:\Dev\odds\data\pollers.sqlite"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    source TEXT NOT NULL,
    item_id TEXT NOT NULL,
    payload_hash TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    notes TEXT,
    PRIMARY KEY (source, item_id)
);
CREATE INDEX IF NOT EXISTS ix_seen_source ON seen(source);
"""


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    try:
        yield c
        c.commit()
    finally:
        c.close()


def is_new(source: str, item_id: str, payload_hash: str = "") -> bool:
    """Returns True the FIRST time this (source, item_id, payload_hash) appears."""
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    with conn() as c:
        row = c.execute(
            "SELECT payload_hash FROM seen WHERE source=? AND item_id=?",
            (source, item_id),
        ).fetchone()
        if row is None:
            c.execute(
                """INSERT INTO seen(source,item_id,payload_hash,first_seen_at,last_seen_at)
                VALUES (?,?,?,?,?)""",
                (source, item_id, payload_hash, now, now),
            )
            return True
        if payload_hash and row["payload_hash"] != payload_hash:
            c.execute(
                """UPDATE seen SET payload_hash=?, last_seen_at=?
                WHERE source=? AND item_id=?""",
                (payload_hash, now, source, item_id),
            )
            return True
        c.execute(
            "UPDATE seen SET last_seen_at=? WHERE source=? AND item_id=?",
            (now, source, item_id),
        )
        return False
