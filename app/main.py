"""
Hardware Comps Dashboard — FastAPI app.

One dashboard, multiple inventory tables (personal_assets, goodwill_flips,
liquidation_pallets, ram_ssd_stockpile, device_farm). eBay comps engine,
per-item price history, RAM/SSD stockpile tracking.

Neon: one project, three databases (hcd_inventory, hcd_comps_cache,
hcd_memory_market). Local SQLite cache for eBay comps (data/).
"""

import asyncio
import json
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# ── Logging ────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("hcd")

# ── Paths ───────────────────────────────────────────────────────────────────

APP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = APP_DIR.parent
DATA_DIR = PROJECT_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# ── Env ─────────────────────────────────────────────────────────────────────

PORT = int(os.environ.get("PORT", "5000"))
SITE_URL = os.environ.get("SITE_URL", "")

# Neon: one project, three databases
APP_NEON_URL = os.environ.get("APP_NEON_URL", "")
INVENTORY_DB_URL = os.environ.get("INVENTORY_DB_URL") or f"{APP_NEON_URL}/hcd_inventory" if APP_NEON_URL else ""
COMPS_CACHE_DB_URL = os.environ.get("COMPS_CACHE_DB_URL") or f"{APP_NEON_URL}/hcd_comps_cache" if APP_NEON_URL else ""
MEMORY_MARKET_DB_URL = os.environ.get("MEMORY_MARKET_DB_URL") or f"{APP_NEON_URL}/hcd_memory_market" if APP_NEON_URL else ""

# Per-DB fallback env (set explicitly if Neon DB-per-URL not usable)
INVENTORY_DB_URL = os.environ.get("INVENTORY_DB_URL", INVENTORY_DB_URL)
COMPS_CACHE_DB_URL = os.environ.get("COMPS_CACHE_DB_URL", COMPS_CACHE_DB_URL)
MEMORY_MARKET_DB_URL = os.environ.get("MEMORY_MARKET_DB_URL", MEMORY_MARKET_DB_URL)

# Local SQLite cache (eBay comps + price history snapshot on the cheap)
COMPS_CACHE_DB = str(DATA_DIR / "comps_cache.db")
PRICE_HISTORY_DB = str(DATA_DIR / "price_history.db")

if not APP_NEON_URL:
    logger.warning("APP_NEON_URL unset — Neon-backed routes will 503 until set")

# eBay (embedded client)
EBAY_CLIENT_ID = os.environ.get("EBAY_CLIENT_ID", "")
EBAY_CLIENT_SECRET = os.environ.get("EBAY_CLIENT_SECRET", "")

# API auth tokens (comma-separated Bearer tokens)
_HCD_API_TOKENS_RAW = os.environ.get("HCD_API_TOKENS", "")
HCD_API_TOKENS = {t.strip() for t in _HCD_API_TOKENS_RAW.split(",") if t.strip()} if _HCD_API_TOKENS_RAW else set()
if not HCD_API_TOKENS:
    logger.warning("HCD_API_TOKENS unset — API routes will reject all requests")

# ── Imports (local modules, lazy so startup fails loud on missing deps) ─────

from app.db_neon import (
    get_inventory_conn,
    get_comps_conn,
    get_memory_conn,
    init_inventory_db,
    init_comps_db,
    init_memory_db,
)
from app.models import (
    InventoryItem,
    InventoryCreate,
    PriceSnapshot,
    CompsLookupRequest,
    MemoryPrice,
    MemoryEvent,
)
from app.ebay_client import eBayClient

ebay = eBayClient()

# ── Lifespan ────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Hardware Comps Dashboard starting")
    init_inventory_db()
    init_comps_db()
    init_memory_db()
    logger.info("DBs initialized")
    yield
    logger.info("Shutting down")


# ── Templates + static ──────────────────────────────────────────────────────

templates = Jinja2Templates(directory=PROJECT_DIR / "templates")
app = FastAPI(
    title="Hardware Comps Dashboard",
    version="0.1.0",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=PROJECT_DIR / "static"), name="static")

# ── CORS ────────────────────────────────────────────────────────────────────

from fastapi.middleware.cors import CORSMiddleware

ALLOWED_ORIGINS = [
    "https://*.replit.dev",
    "https://*.replit.app",
    "https://*.user.repl.co",
    "http://127.0.0.1",
    "http://localhost",
]
REPLIT_DOMAIN = os.environ.get("REPLIT_DEV_DOMAIN", "")
if REPLIT_DOMAIN:
    ALLOWED_ORIGINS.insert(0, f"https://{REPLIT_DOMAIN}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o for o in ALLOWED_ORIGINS if o],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Auth helper ─────────────────────────────────────────────────────────────

def check_token(request: Request) -> str:
    """Return token if valid, else raise 401."""
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing Authorization: Bearer token")
    token = header[7:]
    if token not in HCD_API_TOKENS:
        raise HTTPException(status_code=401, detail="invalid token")
    return token


# ── Lifespan ────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Hardware Comps Dashboard starting")
    init_inventory_db()
    init_comps_db()
    init_memory_db()
    logger.info("DBs initialized")
    yield
    logger.info("Shutting down")


# ── Dashboard (server-rendered) ─────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "request": request,
            "active_table": "personal_assets",
            "tables": [
                ("personal_assets", "Personal Assets"),
                ("goodwill_flips", "Goodwill Flips"),
                ("liquidation_pallets", "Liquidation Pallets"),
                ("ram_ssd_stockpile", "RAM/SSD Stockpile"),
                ("device_farm", "Device Farm"),
            ],
        },
    )


# ── Health ──────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "hardware-comps-dashboard",
        "version": "0.1.0",
        "neon_connected": bool(APP_NEON_URL),
    }


# ── Inventory API ───────────────────────────────────────────────────────────

@app.get("/api/inventory")
async def list_inventory(request: Request, table_type: str = "", limit: int = 50, offset: int = 0):
    check_token(request)
    if table_type and table_type not in VALID_TABLE_TYPES:
        raise HTTPException(400, f"invalid table_type (must be one of {VALID_TABLE_TYPES})")
    items = list_inventory_db(table_type or "", limit, offset)
    total = count_inventory_db(table_type or "")
    return {"items": items, "total": total, "table_type": table_type or "all"}


@app.post("/api/inventory")
async def create_or_update_inventory(request: Request, item: InventoryCreate):
    check_token(request)
    row_id = upsert_inventory_db(item)
    return {"ok": True, "id": row_id}


@app.get("/api/inventory/{item_id}")
async def get_inventory_item(request: Request, item_id: int):
    check_token(request)
    item = get_inventory_db(item_id)
    if not item:
        raise HTTPException(404, "item not found")
    history = list_price_history_db(item_id, limit=60)
    return {"item": item, "price_history": history}


# ── Comps API ───────────────────────────────────────────────────────────────

@app.post("/api/comps/lookup")
async def lookup_comps(request: Request, body: CompsLookupRequest):
    check_token(request)
    if not EBAY_CLIENT_ID or not EBAY_CLIENT_SECRET:
        raise HTTPException(503, "eBay keys not configured")
    result = await ebay.lookup_comps(body.query, body.limit, body.aspects, body.table_type)
    # Write price snapshot
    write_price_snapshot(body.item_id, result)
    return result


@app.get("/api/comps/history")
async def comps_history(request: Request, item_id: int, limit: int = 90):
    check_token(request)
    snapshots = list_price_history_db(item_id, limit)
    return {"snapshots": snapshots}


@app.post("/api/comps/batch")
async def batch_comps(request: Request, body: dict):
    """Run comps for multiple items. Cron-friendly."""
    check_token(request)
    if not EBAY_CLIENT_ID or not EBAY_CLIENT_SECRET:
        raise HTTPException(503, "eBay keys not configured")
    item_ids = body.get("item_ids", [])
    results = []
    for iid in item_ids:
        item = get_inventory_db(iid)
        if not item:
            results.append({"item_id": iid, "error": "not found"})
            continue
        try:
            r = await ebay.lookup_comps(item.search_query, 50, None, item.table_type)
            write_price_snapshot(iid, r)
            results.append({"item_id": iid, "ok": True, "sold_avg": r.get("sold_avg"), "active_avg": r.get("active_avg")})
        except Exception as e:
            results.append({"item_id": iid, "error": str(e)})
    # Write memory market feed for metals dashboard
    write_memory_market_feed()
    return {"results": results}


# ── Memory market API (also feeds metals dashboard) ─────────────────────────

@app.get("/api/memory-market")
async def memory_market(request: Request):
    check_token(request)
    prices = list_memory_prices_db(limit=180)
    events = list_memory_events_db(limit=50)
    return {"prices": prices, "events": events, "generated_at": datetime.now(timezone.utc).isoformat()}


# ── Static dashboard JS data endpoint (lightweight, no auth for page render) ─

@app.get("/api/dashboard-data")
async def dashboard_data(request: Request):
    """Lightweight data for the dashboard page (called by JS after page load).
    Auth gated — same token check."""
    check_token(request)
    active_table = request.query_params.get("table", "personal_assets")
    items = list_inventory_db(active_table, limit=100, offset=0)
    return {"items": items, "table": active_table}


# ── DB helpers (Neon-backed) ────────────────────────────────────────────────

VALID_TABLE_TYPES = {
    "personal_assets",
    "goodwill_flips",
    "liquidation_pallets",
    "ram_ssd_stockpile",
    "device_farm",
}


def list_inventory_db(table_type: str, limit: int, offset: int):
    """Neon-backed: list inventory items, optionally filtered by table_type."""
    if not INVENTORY_DB_URL:
        return []
    try:
        conn = get_inventory_conn()
        if table_type:
            rows = conn.execute(
                """SELECT * FROM inventory_items
                   WHERE table_type = ? AND deleted_at IS NULL
                   ORDER BY updated_at DESC LIMIT ? OFFSET ?""",
                (table_type, limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM inventory_items
                   WHERE deleted_at IS NULL
                   ORDER BY updated_at DESC LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Inventory list DB error: {e}")
        return []


def count_inventory_db(table_type: str):
    if not INVENTORY_DB_URL:
        return 0
    try:
        conn = get_inventory_conn()
        if table_type:
            n = conn.execute(
                "SELECT COUNT(*) FROM inventory_items WHERE table_type = ? AND deleted_at IS NULL",
                (table_type,),
            ).fetchone()[0]
        else:
            n = conn.execute(
                "SELECT COUNT(*) FROM inventory_items WHERE deleted_at IS NULL"
            ).fetchone()[0]
        conn.close()
        return n
    except Exception as e:
        logger.error(f"Inventory count DB error: {e}")
        return 0


def get_inventory_db(item_id: int):
    if not INVENTORY_DB_URL:
        return None
    try:
        conn = get_inventory_conn()
        row = conn.execute(
            "SELECT * FROM inventory_items WHERE id = ?", (item_id,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception as e:
        logger.error(f"Inventory get DB error: {e}")
        return None


def upsert_inventory_db(item: InventoryCreate):
    if not INVENTORY_DB_URL:
        raise RuntimeError("Neon inventory DB not configured")
    try:
        conn = get_inventory_conn()
        now = datetime.now(timezone.utc)
        if item.id:
            conn.execute(
                """UPDATE inventory_items SET
                   table_type=?, name=?, category=?, make=?, model=?,
                   serial_number=?, condition=?, notes=?,
                   acquisition_cost_cents=?, buy_source=?,
                   ebay_active_avg_cents=?, ebay_sold_avg_cents=?,
                   ebay_last_looked_at=?, search_query=?,
                   updated_at=?, metadata_json=?
                   WHERE id=?""",
                (
                    item.table_type, item.name, item.category, item.make, item.model,
                    item.serial_number, item.condition, item.notes,
                    item.acquisition_cost_cents, item.buy_source,
                    item.ebay_active_avg_cents, item.ebay_sold_avg_cents,
                    item.ebay_last_looked_at, item.search_query,
                    now, json.dumps(item.metadata_json or {}),
                    item.id,
                ),
            )
            conn.commit()
            conn.close()
            return item.id
        else:
            row = conn.execute(
                """INSERT INTO inventory_items
                   (table_type, name, category, make, model, serial_number,
                    condition, notes, acquisition_cost_cents, buy_source,
                    ebay_active_avg_cents, ebay_sold_avg_cents,
                    ebay_last_looked_at, search_query, created_at, updated_at, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    item.table_type, item.name, item.category, item.make, item.model,
                    item.serial_number, item.condition, item.notes,
                    item.acquisition_cost_cents, item.buy_source,
                    item.ebay_active_avg_cents, item.ebay_sold_avg_cents,
                    item.ebay_last_looked_at, item.search_query,
                    now, now, json.dumps(item.metadata_json or {}),
                ),
            )
            conn.commit()
            new_id = row.lastrowid
            conn.close()
            return new_id
    except Exception as e:
        logger.error(f"Inventory upsert DB error: {e}")
        raise HTTPException(500, str(e))


def write_price_snapshot(item_id: int, result: dict):
    """Write an eBay comp result as a price_history row (Neon)."""
    if not INVENTORY_DB_URL:
        return
    try:
        conn = get_inventory_conn()
        now = datetime.now(timezone.utc)
        sold_prices = [i["price"] for i in result.get("items", []) if i.get("price")]
        active_prices = [i["price"] for i in result.get("active_items", []) if i.get("price")]
        sold_avg = round(sum(sold_prices) / len(sold_prices), 2) if sold_prices else 0
        active_avg = round(sum(active_prices) / len(active_prices), 2) if active_prices else 0
        conn.execute(
            """INSERT INTO price_history
               (item_id, snapshot_at, sold_avg_cents, active_avg_cents,
                sold_count, active_count, comps_query, comps_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                item_id, now,
                int(sold_avg * 100), int(active_avg * 100),
                len(sold_prices), len(active_prices),
                result.get("query", ""),
                json.dumps(result),
            ),
        )
        # Update item's latest comp averages
        conn.execute(
            """UPDATE inventory_items SET
               ebay_sold_avg_cents=?, ebay_active_avg_cents=?,
               ebay_last_looked_at=? WHERE id=?""",
            (int(sold_avg * 100), int(active_avg * 100), now, item_id),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Price snapshot write error: {e}")


def list_price_history_db(item_id: int, limit: int):
    if not INVENTORY_DB_URL:
        return []
    try:
        conn = get_inventory_conn()
        rows = conn.execute(
            """SELECT * FROM price_history
               WHERE item_id = ? ORDER BY snapshot_at DESC LIMIT ?""",
            (item_id, limit),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Price history DB error: {e}")
        return []


# ── Memory market feed (for metals dashboard) ──────────────────────────────

def write_memory_market_feed():
    """Write a lightweight JSON snapshot of current memory prices for the
    infra-metals-dashboard cron to pick up as a demand-side signal."""
    prices = list_memory_prices_db(limit=90)
    snapshot_path = DATA_DIR / "memory_market_feed.json"
    try:
        data = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "prices": prices,
        }
        snapshot_path.write_text(json.dumps(data, default=str))
        logger.info(f"Memory market feed written: {snapshot_path}")
    except Exception as e:
        logger.error(f"Memory market feed write error: {e}")


# ── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=PORT, log_level="info")
