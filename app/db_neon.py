"""
Neon multi-DB connection helper for Hardware Comps Dashboard.

One Neon project, three databases:
  - hcd_inventory    : inventory_items, price_history
  - hcd_comps_cache  : eBay comps cache (query-keyed)
  - hcd_memory_market: DDR3/4/5 reference spot prices + supply-side events

Env:
  APP_NEON_URL       — project base URL (same-project DBs share this)
  INVENTORY_DB_URL   — override for inventory DB URL
  COMPS_CACHE_DB_URL — override for comps cache DB URL
  MEMORY_MARKET_DB_URL — override for memory market DB URL

Same-project Neon DBs are reachable as {APP_NEON_URL}/<dbname> when the
project allows it; otherwise set each DB URL explicitly via env.
"""

import logging
import os
from functools import lru_cache

import psycopg2
from psycopg2 import sql

logger = logging.getLogger("hcd.db_neon")

# ── Connection URLs ──────────────────────────────────────────────────────────

# Read from env; fall back to APP_NEON_URL-derived URLs.
_APP_NEON_URL = os.environ.get("APP_NEON_URL", "")

_INVENTORY_DB_URL = os.environ.get("INVENTORY_DB_URL") or (
    f"{_APP_NEON_URL}/hcd_inventory" if _APP_NEON_URL else ""
)
_COMPS_CACHE_DB_URL = os.environ.get("COMPS_CACHE_DB_URL") or (
    f"{_APP_NEON_URL}/hcd_comps_cache" if _APP_NEON_URL else ""
)
_MEMORY_MARKET_DB_URL = os.environ.get("MEMORY_MARKET_DB_URL") or (
    f"{_APP_NEON_URL}/hcd_memory_market" if _APP_NEON_URL else ""
)

# Allow explicit override even if derived URL looks plausible
if os.environ.get("INVENTORY_DB_URL"):
    _INVENTORY_DB_URL = os.environ["INVENTORY_DB_URL"]
if os.environ.get("COMPS_CACHE_DB_URL"):
    _COMPS_CACHE_DB_URL = os.environ["COMPS_CACHE_DB_URL"]
if os.environ.get("MEMORY_MARKET_DB_URL"):
    _MEMORY_MARKET_DB_URL = os.environ["MEMORY_MARKET_DB_URL"]


def _url_is_usable(url: str) -> bool:
    return bool(url and url.startswith("postgresql://"))


# ── Per-DB connection cache (process-local, since Neon HTTP is not used here) ─

@lru_cache(maxsize=3)
def _cached_conn(url: str):
    """Return a psycopg2 connection for the given URL. Cached per URL."""
    if not _url_is_usable(url):
        raise RuntimeError(f"Bad DB URL: {url[:40]}...")
    return psycopg2.connect(url)


def get_inventory_conn():
    if not _url_is_usable(_INVENTORY_DB_URL):
        raise RuntimeError("INVENTORY_DB_URL not configured")
    return _cached_conn(_INVENTORY_DB_URL)


def get_comps_conn():
    if not _url_is_usable(_COMPS_CACHE_DB_URL):
        raise RuntimeError("COMPS_CACHE_DB_URL not configured")
    return _cached_conn(_COMPS_CACHE_DB_URL)


def get_memory_conn():
    if not _url_is_usable(_MEMORY_MARKET_DB_URL):
        raise RuntimeError("MEMORY_MARKET_DB_URL not configured")
    return _cached_conn(_MEMORY_MARKET_DB_URL)


# ── Schema init ──────────────────────────────────────────────────────────────

def init_inventory_db():
    """Create inventory schema tables if they don't exist."""
    if not _url_is_usable(_INVENTORY_DB_URL):
        logger.warning("INVENTORY_DB_URL not set — skipping inventory init")
        return
    conn = get_inventory_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS inventory_items (
                id                  SERIAL PRIMARY KEY,
                table_type          TEXT NOT NULL CHECK (table_type IN (
                    'personal_assets', 'goodwill_flips',
                    'liquidation_pallets', 'ram_ssd_stockpile', 'device_farm'
                )),
                name                TEXT NOT NULL,
                category            TEXT,
                make                TEXT,
                model               TEXT,
                serial_number       TEXT,
                condition           TEXT,
                notes               TEXT,
                acquisition_cost_cents INTEGER,
                buy_source          TEXT,
                ebay_active_avg_cents  INTEGER,
                ebay_sold_avg_cents    INTEGER,
                ebay_last_looked_at    TIMESTAMPTZ,
                search_query           TEXT,
                metadata_json          JSONB,
                created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                deleted_at            TIMESTAMPTZ
            );

            CREATE INDEX IF NOT EXISTS inventory_table_type_idx
                ON inventory_items (table_type, updated_at DESC)
                WHERE deleted_at IS NULL;

            CREATE INDEX IF NOT EXISTS inventory_name_idx
                ON inventory_items (name);

            CREATE TABLE IF NOT EXISTS price_history (
                id              SERIAL PRIMARY KEY,
                item_id         INTEGER NOT NULL REFERENCES inventory_items(id),
                snapshot_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                sold_avg_cents  INTEGER,
                active_avg_cents INTEGER,
                sold_count      INTEGER,
                active_count    INTEGER,
                comps_query     TEXT,
                comps_json      JSONB,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE INDEX IF NOT EXISTS price_history_item_idx
                ON price_history (item_id, snapshot_at DESC);

            CREATE INDEX IF NOT EXISTS price_history_at_idx
                ON price_history (snapshot_at DESC);
        """)
        conn.commit()
        logger.info("Inventory DB initialized")
    except Exception as e:
        logger.error(f"Inventory DB init failed: {e}")
    finally:
        conn.close()


def init_comps_db():
    if not _url_is_usable(_COMPS_CACHE_DB_URL):
        logger.warning("COMPS_CACHE_DB_URL not set — skipping comps cache init")
        return
    conn = get_comps_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS comps_cache (
                id          SERIAL PRIMARY KEY,
                cache_key   TEXT NOT NULL UNIQUE,
                query_text  TEXT NOT NULL,
                endpoint    TEXT NOT NULL,          -- 'comps' or 'sold'
                response_json JSONB NOT NULL,
                item_count  INTEGER,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                expires_at  TIMESTAMPTZ NOT NULL
            );

            CREATE INDEX IF NOT EXISTS comps_cache_key_idx
                ON comps_cache (cache_key);

            CREATE INDEX IF NOT EXISTS comps_cache_expires_idx
                ON comps_cache (expires_at);

            CREATE TABLE IF NOT EXISTS comps_rate_limits (
                token_hash    TEXT NOT NULL,
                window_start  TIMESTAMPTZ NOT NULL,
                request_count INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (token_hash, window_start)
            );
        """)
        conn.commit()
        logger.info("Comps cache DB initialized")
    except Exception as e:
        logger.error(f"Comps cache DB init failed: {e}")
    finally:
        conn.close()


def init_memory_db():
    if not _url_is_usable(_MEMORY_MARKET_DB_URL):
        logger.warning("MEMORY_MARKET_DB_URL not set — skipping memory market init")
        return
    conn = get_memory_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memory_prices (
                id              SERIAL PRIMARY KEY,
                generation      TEXT NOT NULL CHECK (generation IN ('DDR3','DDR4','DDR5')),
                form_factor     TEXT,                          -- 'SODIMM','DIMM','M.2','2.5"','U.2'
                capacity_gb     INTEGER,
                speed_mhz       TEXT,
                spot_avg_cents  INTEGER,                       -- rolling comp average
                source_query    TEXT,
                observed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE INDEX IF NOT EXISTS memory_prices_gen_idx
                ON memory_prices (generation, observed_at DESC);

            CREATE INDEX IF NOT EXISTS memory_prices_full_idx
                ON memory_prices (generation, form_factor, capacity_gb, speed_mhz, observed_at DESC);

            CREATE TABLE IF NOT EXISTS memory_events (
                id             SERIAL PRIMARY KEY,
                event_type     TEXT NOT NULL,                 -- 'tariff','fab_capacity','price_spike','price_drop'
                generation     TEXT,                          -- 'DDR3','DDR4','DDR5' or NULL for general
                headline       TEXT NOT NULL,
                body           TEXT,
                source_url     TEXT,
                observed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE INDEX IF NOT EXISTS memory_events_observed_idx
                ON memory_events (observed_at DESC);
        """)
        conn.commit()
        logger.info("Memory market DB initialized")
    except Exception as e:
        logger.error(f"Memory market DB init failed: {e}")
    finally:
        conn.close()
