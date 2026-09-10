# Hardware Comps Dashboard

FastAPI + static HTML dashboard for tracking resale-relevant hardware inventory
with live eBay comps, per-item price history trends, and a RAM/SSD stockpile tracker.

## Stack

- FastAPI (Python 3.11+)
- Jinja2 server-rendered HTML (dark mode, design DNA from Replit starter)
- eBay Browse API (embedded OAuth2 client, not via proxy)
- Neon PostgreSQL — one project, three databases:
  - `hcd_inventory` — inventory tables + per-item price history
  - `hcd_comps_cache` — eBay comps cache (query-keyed, TTL)
  - `hcd_memory_market` — DDR3/4/5 reference spot prices, supply-side events
- Replit deployment (uv, $PORT, SITE_URL)

## Inventory tables (all in hcd_inventory, unified via `table_type`)

| table_type          | Purpose                                                  |
|---------------------|----------------------------------------------------------|
| personal_assets     | Owned gear, camera equipment, storage-unit contents      |
| goodwill_flips      | Buy-to-resell items sourced from Goodwill/thrift         |
| liquidation_pallets | Pallet manifests + valuation vs. asking price            |
| ram_ssd_stockpile   | DDR3/4/5 RAM and SSDs being held for appreciation        |
| device_farm         | Real devices (iPhone/etc.) used for QA / scraping        |

## Key routes

- `GET  /`                    — Dashboard (table switcher + main table)
- `GET  /api/inventory`       — Paginated inventory items (filterable by table_type)
- `POST /api/inventory`       — Add / update an inventory item
- `GET  /api/inventory/{id}`  — Single item detail + price history
- `POST /api/comps/lookup`    — Run eBay comps for an item, store price snapshot
- `GET  /api/comps/history`   — Price history for an item (trend data)
- `GET  /api/memory-market`   — DDR3/4/5 reference prices (for RAM/SSD tab + metals feed)
- `POST /api/comps/batch`     — Run comps for multiple items (cron-friendly)
- `GET  /health`              — Liveness

## Reused from existing repos

| Source                   | Carried over                                      |
|--------------------------|---------------------------------------------------|
| ebay-api-proxy           | `ebay_client.py` (OAuth2 + Browse API), `cache.py` (SQLite cache pattern) |
| thrift-lens              | Comps cache table design, EbayComp shape, per-item valuation via comps pattern |
| gear-rental              | eBay active+sold avg → replacement value pattern, equipment valuation UX |
| replit-starter-template  | FastAPI scaffold, `app/proxy.py`, Replit deploy config, design DNA |

## Memory market → infra-metals-dashboard

The RAM/SSD stockpile comps data and DDR generation spot prices in `hcd_memory_market`
are written to a shared JSON snapshot (`data/memory_market_feed.json`) that the
infra-metals-dashboard cron picks up as a demand-side signal.
