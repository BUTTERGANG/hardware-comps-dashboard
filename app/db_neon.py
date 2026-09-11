"""
Single Neon DB connection helper for Hardware Comps Dashboard.

One Neon database holds everything durable:
  - inventory_items, price_history          — user inventory + trend data
  - comps_cache, comps_rate_limits          — eBay comps cache (local SQLite
                                              handles the hot path; this is
                                              the Neon-side backup mirror)
  - memory_prices, memory_events            — DDR generation spot prices,
                                              supply-side event log

Local SQLite (data/comps_cache.db) still handles the hot eBay-response cache
for resilience; this Neon DB is the durable store.

Env:
  DATABASE_URL  — single Neon connection URL (postgresql://...)
                  Also accepted as APP_NEON_URL for compat with earlier config.
"""

import logging
import os
import psycopg
from psycopg.rows import dict_row

logger = logging.getLogger("hcd.db_neon")

_DATABASE_URL = os.environ.get("DATABASE_URL") or os.environ.get("APP_NEON_URL", "")

if os.environ.get("DATABASE_URL"):
    _DATABASE_URL = os.environ["DATABASE_URL"]


def _url_is_usable(url: str) -> bool:
    return bool(url and url.startswith("postgresql://"))


def _conn():
    if not _url_is_usable(_DATABASE_URL):
        raise RuntimeError(f"DATABASE_URL not configured (got: {_DATABASE_URL[:40]}...)")
    return psycopg.connect(_DATABASE_URL, row_factory=dict_row)


def get_conn():
    return _conn()


def init_db():
    """Create all schema tables in one Neon DB."""
    if not _url_is_usable(_DATABASE_URL):
        logger.warning("DATABASE_URL not set — skipping DB init")
        return
    conn = get_conn()
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

            CREATE TABLE IF NOT EXISTS comps_cache (
                id          SERIAL PRIMARY KEY,
                cache_key   TEXT NOT NULL UNIQUE,
                query_text  TEXT NOT NULL,
                endpoint    TEXT NOT NULL,
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

            CREATE TABLE IF NOT EXISTS memory_prices (
                id              SERIAL PRIMARY KEY,
                generation      TEXT NOT NULL CHECK (generation IN ('DDR3','DDR4','DDR5')),
                form_factor     TEXT,
                capacity_gb     INTEGER,
                speed_mhz       TEXT,
                spot_avg_cents  INTEGER,
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
                event_type     TEXT NOT NULL,
                generation     TEXT,
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
        logger.info("Single Neon DB initialized (all tables)")
    except Exception as e:
        logger.error(f"DB init failed: {e}")
    finally:
        conn.close()
