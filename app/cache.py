"""
SQLite-backed comps cache + price history snapshot for Hardware Comps Dashboard.

Two roles:
1. Comps cache (comps_cache.db) — eBay comp results, 7-day TTL, SHA-256 query key.
   Mirrors ebay-api-proxy/cache.py semantics so we don't hit eBay on every lookup.
2. Price history snapshot (price_history.db) — lightweight local fallback when Neon
   is unreachable; holds recent per-item snapshots so the dashboard can still render
   trend arrows even offline.

In normal operation Neon holds the source of truth; these SQLite files are the fast
local cache + offline fallback.
"""

import hashlib
import json
import logging
import os
import sqlite3
import time

logger = logging.getLogger("hcd.cache")

DEFAULT_COMBS_DB = os.environ.get("COMPS_CACHE_DB", "data/comps_cache.db")
DEFAULT_HISTORY_DB = os.environ.get("PRICE_HISTORY_DB", "data/price_history.db")

COMBS_TTL = 7 * 24 * 3600  # 7 days
HISTORY_LIMIT_PER_ITEM = 180  # keep last 180 snapshots per item locally


def _conn(db_path: str):
    p = db_path if os.path.isabs(db_path) else os.path.join(os.getcwd(), db_path)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_comps_db(db_path: str = DEFAULT_COMBS_DB):
    p = db_path if os.path.isabs(db_path) else os.path.join(os.getcwd(), db_path)
    conn = sqlite3.connect(p)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS cache_entries (
            cache_key TEXT PRIMARY KEY,
            query_text TEXT,
            endpoint TEXT,
            response_json TEXT,
            item_count INTEGER,
            created_at REAL,
            expires_at REAL,
            hit_count INTEGER DEFAULT 1
        );
        CREATE INDEX IF NOT EXISTS idx_comps_expires ON cache_entries(expires_at);
        CREATE INDEX IF NOT EXISTS idx_comps_query ON cache_entries(query_text, endpoint);
    """)
    conn.commit()
    conn.close()
    logger.info(f"Comps cache DB initialized: {p}")


def init_history_db(db_path: str = DEFAULT_HISTORY_DB):
    p = db_path if os.path.isabs(db_path) else os.path.join(os.getcwd(), db_path)
    conn = sqlite3.connect(p)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS price_snapshots (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id     INTEGER NOT NULL,
            snapshot_at REAL NOT NULL,
            sold_avg    REAL,
            active_avg  REAL,
            sold_count  INTEGER,
            active_count INTEGER,
            comps_query TEXT,
            comps_json  TEXT,
            UNIQUE(item_id, snapshot_at)
        );
        CREATE INDEX IF NOT EXISTS idx_history_item ON price_snapshots(item_id, snapshot_at DESC);
    """)
    # Prune old snapshots beyond LOCAL limit
    conn.execute(
        f"DELETE FROM price_snapshots WHERE id NOT IN (SELECT id FROM price_snapshots WHERE item_id IN (SELECT DISTINCT item_id FROM price_snapshots) ORDER BY item_id, snapshot_at DESC LIMIT {HISTORY_LIMIT_PER_ITEM} * (SELECT COUNT(DISTINCT item_id) FROM price_snapshots))"
    )
    conn.commit()
    conn.close()
    logger.info(f"Price history DB initialized: {p}")


def _normalize_query(query: str) -> str:
    return " ".join(query.lower().split())


def _make_cache_key(query: str, endpoint: str) -> str:
    raw = f"{_normalize_query(query)}|{endpoint}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def get_comps(query: str, endpoint: str = "comps"):
    now = time.time()
    key = _make_cache_key(query, endpoint)
    conn = _conn(DEFAULT_COMBS_DB)
    try:
        row = conn.execute(
            "SELECT response_json, expires_at FROM cache_entries WHERE cache_key = ?",
            (key,),
        ).fetchone()
        if not row or row["expires_at"] < now:
            return None
        data = json.loads(row["response_json"])
        data["_cached"] = True
        data["_cache_key"] = key
        conn.execute("UPDATE cache_entries SET hit_count = hit_count + 1 WHERE cache_key = ?", (key,))
        conn.commit()
        return data
    finally:
        conn.close()


def set_comps(query: str, endpoint: str, response: dict, ttl: int = COMBS_TTL):
    now = time.time()
    key = _make_cache_key(query, endpoint)
    conn = _conn(DEFAULT_COMBS_DB)
    try:
        clean = {k: v for k, v in response.items() if not k.startswith("_")}
        conn.execute(
            """INSERT OR REPLACE INTO cache_entries
               (cache_key, query_text, endpoint, response_json, item_count, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                key, _normalize_query(query), endpoint,
                json.dumps(clean), len(clean.get("items", [])) + len(clean.get("active_items", [])),
                now, now + ttl,
            ),
        )
        conn.commit()
        logger.debug(f"Cached {endpoint}/{key} ({ttl}s TTL)")
    finally:
        conn.close()


def write_snapshot(item_id: int, sold_avg: float, active_avg: float, sold_count: int, active_count: int, query: str, comps_json: dict):
    """Write a per-item price snapshot into the local price_history DB (offline fallback)."""
    now = time.time()
    conn = _conn(DEFAULT_HISTORY_DB)
    try:
        conn.execute(
            """INSERT OR IGNORE INTO price_snapshots
               (item_id, snapshot_at, sold_avg, active_avg, sold_count, active_count, comps_query, comps_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (item_id, now, sold_avg, active_avg, sold_count, active_count, query, json.dumps(comps_json)),
        )
        conn.commit()
    finally:
        conn.close()


def get_local_history(item_id: int, limit: int = 90):
    """Read recent price snapshots from local DB (offline fallback)."""
    conn = _conn(DEFAULT_HISTORY_DB)
    try:
        rows = conn.execute(
            "SELECT * FROM price_snapshots WHERE item_id = ? ORDER BY snapshot_at DESC LIMIT ?",
            (item_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
