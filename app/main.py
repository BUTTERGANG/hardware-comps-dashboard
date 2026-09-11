"""
Hardware Comps Dashboard — FastAPI app.

One dashboard, multiple inventory tables (personal_assets, goodwill_flips,
liquidation_pallets, ram_ssd_stockpile, device_farm). eBay comps engine,
per-item price history, RAM/SSD stockpile tracking.

Neon: single database (DATABASE_URL). Local SQLite cache for eBay comps (data/).
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

# Neon: single DB (DATABASE_URL or APP_NEON_URL for compat)
APP_NEON_URL = os.environ.get("APP_NEON_URL", "")
DATABASE_URL = os.environ.get("DATABASE_URL") or APP_NEON_URL

# Local SQLite cache (eBay comps — hot path, resilience when Neon is down)
COMPS_CACHE_DB = str(DATA_DIR / "comps_cache.db")

# eBay (embedded client)
EBAY_CLIENT_ID = os.environ.get("EBAY_CLIENT_ID", "")
EBAY_CLIENT_SECRET = os.environ.get("EBAY_CLIENT_SECRET", "")

# API auth tokens (comma-separated Bearer tokens)
_HCD_API_TOKENS_RAW = os.environ.get("HCD_API_TOKENS", "")
HCD_API_TOKENS = {t.strip() for t in _HCD_API_TOKENS_RAW.split(",") if t.strip()} if _HCD_API_TOKENS_RAW else set()
if not HCD_API_TOKENS:
    logger.warning("HCD_API_TOKENS unset — API routes will reject all requests")

# ── Imports (local modules, lazy so startup fails loud on missing deps) ─────

from app.db_neon import get_conn, init_db
from app.models import (
    InventoryItem,
    InventoryCreate,
    PriceSnapshot,
    CompsLookupRequest,
    MemoryPrice,
    MemoryEvent,
    SearchResult,
    IdentificationResult,
)
from app.ebay_client import eBayClient
from app.claude_client import ClaudeClient, identify_from_image, analyze_item, identify_and_comps

ebay = eBayClient()
claude = ClaudeClient()

# ── Lifespan ────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Hardware Comps Dashboard starting")
    init_db()
    logger.info("DB initialized")
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
    init_db()
    logger.info("DB initialized")
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


# ── Search API (text + photo) ────────────────────────────────────────────────

@app.get("/api/search")
async def search_comps(request: Request, q: str = "", limit: int = 20):
    """Text search → eBay comps. No item needed; returns comps + averages.

    Query param `q` is the eBay search query (e.g. "Samsung 8GB DDR4 SODIMM").
    Returns active + sold listings, averages, and a suggested search_query.
    """
    check_token(request)
    if not q or not q.strip():
        raise HTTPException(400, "query parameter 'q' is required")
    if not EBAY_CLIENT_ID or not EBAY_CLIENT_SECRET:
        raise HTTPException(503, "eBay keys not configured")
    query = q.strip()
    result = await ebay.lookup_comps(query, limit)
    # Normalize result to the SearchResult shape
    return {
        "query": query,
        "active_items": result.get("active_items", []),
        "sold_items": result.get("sold_items", []),
        "active_avg": result.get("active_avg", 0),
        "sold_avg": result.get("sold_avg", 0),
        "active_count": result.get("active_count", 0),
        "sold_count": result.get("sold_count", 0),
        "suggested_query": result.get("query", query),
    }


@app.post("/api/search/photo")
async def search_by_photo(request: Request, body: dict):
    """Photo → identify → eBay comps → pricing analysis.

    Expects body: {"image_b64": "<base64>", "image_mime": "image/jpeg", "limit": 50}
    Returns: {identification, comps, analysis}
    """
    check_token(request)
    if not EBAY_CLIENT_ID or not EBAY_CLIENT_SECRET:
        raise HTTPException(503, "eBay keys not configured")
    if not CLAUDE_API_KEY:
        raise HTTPException(503, "Claude API key not configured")
    image_b64 = body.get("image_b64", "")
    image_mime = body.get("image_mime", "image/jpeg")
    limit = body.get("limit", 50)
    if not image_b64:
        raise HTTPException(400, "image_b64 is required")
    import base64
    try:
        image_bytes = base64.b64decode(image_b64)
    except Exception as e:
        raise HTTPException(400, f"invalid base64 image: {e}")
    if len(image_bytes) > 10 * 1024 * 1024:
        raise HTTPException(400, "image too large (max 10MB)")
    try:
        result = await identify_and_comps(image_bytes, image_mime, ebay_client=ebay, comps_limit=limit, do_refinement=True)
        return result
    except RuntimeError as e:
        if "CLAUDE_API_KEY" in str(e):
            raise HTTPException(503, "Claude API key not configured")
        raise HTTPException(502, str(e))
    except Exception as e:
        logger.error(f"Photo search error: {e}")
        raise HTTPException(502, str(e))


@app.post("/api/search/save-identification")
async def save_identification(request: Request, body: dict):
    """Save a photo-identification result as an inventory item.

    Expects body: {"identification": {...}, "comps": {...}, "analysis": {...},
                   "table_type": "goodwill_flips", "acquisition_cost_cents": 500, ...}
    Creates an inventory item from the identification, writes a price snapshot,
    and returns the item ID.
    """
    check_token(request)
    identification = body.get("identification", {})
    comps = body.get("comps", {})
    table_type = body.get("table_type", "goodwill_flips")
    name = identification.get("item_name") or "Unknown item"
    make = identification.get("brand")
    model = identification.get("model")
    category = identification.get("category")
    condition = identification.get("condition", "unknown")
    search_query = identification.get("ebay_search_query") or name
    acq_cost = body.get("acquisition_cost_cents")
    buy_source = body.get("buy_source", "Photo scan")
    notes = body.get("notes", "")

    item = InventoryCreate(
        id=None,
        table_type=table_type,
        name=name,
        make=make,
        model=model,
        category=category,
        condition=condition,
        notes=notes,
        acquisition_cost_cents=acq_cost,
        buy_source=buy_source,
        search_query=search_query,
        ebay_active_avg_cents=int(comps.get("active_avg", 0) * 100) if comps.get("active_avg") else None,
        ebay_sold_avg_cents=int(comps.get("sold_avg", 0) * 100) if comps.get("sold_avg") else None,
        ebay_last_looked_at=datetime.now(timezone.utc).isoformat(),
        metadata_json={
            "identification": identification,
            "comps": comps,
            "analysis": body.get("analysis", {}),
        },
    )
    item_id = upsert_inventory_db(item)
    # Write price snapshot
    write_price_snapshot(item_id, comps)
    return {"ok": True, "id": item_id, "item": get_inventory_db(item_id)}


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


@app.post("/api/memory-market")
async def log_memory_event(request: Request, body: dict):
    """Log a supply-side / price event for the memory market."""
    check_token(request)
    event_type = body.get("event_type", "price_spike")
    generation = body.get("generation") or None
    headline = body.get("headline", "")
    body_text = body.get("body") or None
    source_url = body.get("source_url") or None
    if not headline:
        raise HTTPException(400, "headline is required")
    write_memory_event(event_type, generation, headline, body_text, source_url)
    return {"ok": True, "event_type": event_type, "headline": headline}


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
    if not DATABASE_URL:
        return []
    try:
        conn = get_conn()
        if table_type:
            rows = conn.execute(
                """SELECT * FROM inventory_items
                   WHERE table_type = %s AND deleted_at IS NULL
                   ORDER BY updated_at DESC LIMIT %s OFFSET %s""",
                (table_type, limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM inventory_items
                   WHERE deleted_at IS NULL
                   ORDER BY updated_at DESC LIMIT %s OFFSET %s""",
                (limit, offset),
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Inventory list DB error: {e}")
        return []


def count_inventory_db(table_type: str):
    if not DATABASE_URL:
        return 0
    try:
        conn = get_conn()
        if table_type:
            n = conn.execute(
                "SELECT COUNT(*) FROM inventory_items WHERE table_type = %s AND deleted_at IS NULL",
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
    if not DATABASE_URL:
        return None
    try:
        conn = get_conn()
        row = conn.execute(
            "SELECT * FROM inventory_items WHERE id = %s", (item_id,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception as e:
        logger.error(f"Inventory get DB error: {e}")
        return None


def upsert_inventory_db(item: InventoryCreate):
    if not DATABASE_URL:
        raise RuntimeError("Neon DB not configured (DATABASE_URL)")
    try:
        conn = get_conn()
        now = datetime.now(timezone.utc)
        if item.id:
            conn.execute(
                """UPDATE inventory_items SET
                   table_type=%s, name=%s, category=%s, make=%s, model=%s,
                   serial_number=%s, condition=%s, notes=%s,
                   acquisition_cost_cents=%s, buy_source=%s,
                   ebay_active_avg_cents=%s, ebay_sold_avg_cents=%s,
                   ebay_last_looked_at=%s, search_query=%s,
                   updated_at=%s, metadata_json=%s
                   WHERE id=%s""",
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
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id""",
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
            new_id = row.fetchone()["id"]
            conn.close()
            return new_id
    except Exception as e:
        logger.error(f"Inventory upsert DB error: {e}")
        raise HTTPException(500, str(e))


def write_price_snapshot(item_id: int, result: dict):
    """Write an eBay comp result as a price_history row (Neon)."""
    if not DATABASE_URL:
        return
    try:
        conn = get_conn()
        now = datetime.now(timezone.utc)
        sold_prices = [i["price"] for i in result.get("sold_items", []) if i.get("price")]
        active_prices = [i["price"] for i in result.get("active_items", []) if i.get("price")]
        sold_avg = round(sum(sold_prices) / len(sold_prices), 2) if sold_prices else 0
        active_avg = round(sum(active_prices) / len(active_prices), 2) if active_prices else 0
        conn.execute(
            """INSERT INTO price_history
               (item_id, snapshot_at, sold_avg_cents, active_avg_cents,
                sold_count, active_count, comps_query, comps_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (item_id, snapshot_at) DO NOTHING""",
            (
                item_id, now,
                int(sold_avg * 100), int(active_avg * 100),
                len(sold_prices), len(active_prices),
                result.get("query", ""),
                json.dumps(result),
            ),
        )
        conn.execute(
            """UPDATE inventory_items SET
               ebay_sold_avg_cents=%s, ebay_active_avg_cents=%s,
               ebay_last_looked_at=%s WHERE id=%s""",
            (int(sold_avg * 100), int(active_avg * 100), now, item_id),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Price snapshot write error: {e}")


def list_price_history_db(item_id: int, limit: int):
    if not DATABASE_URL:
        return []
    try:
        conn = get_conn()
        rows = conn.execute(
            """SELECT * FROM price_history
               WHERE item_id = %s ORDER BY snapshot_at DESC LIMIT %s""",
            (item_id, limit),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Price history DB error: {e}")
        return []


def list_memory_prices_db(limit: int):
    if not DATABASE_URL:
        return []
    try:
        conn = get_conn()
        rows = conn.execute(
            """SELECT generation, form_factor, capacity_gb, speed_mhz,
                    spot_avg_cents, source_query, observed_at
               FROM memory_prices
               ORDER BY observed_at DESC LIMIT %s""",
            (limit,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Memory prices DB error: {e}")
        return []


def list_memory_events_db(limit: int):
    if not DATABASE_URL:
        return []
    try:
        conn = get_conn()
        rows = conn.execute(
            """SELECT event_type, generation, headline, body, source_url, observed_at
               FROM memory_events
               ORDER BY observed_at DESC LIMIT %s""",
            (limit,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Memory events DB error: {e}")
        return []


def write_memory_event(event_type: str, generation: str | None, headline: str, body: str | None, source_url: str | None):
    if not DATABASE_URL:
        return
    try:
        conn = get_conn()
        now = datetime.now(timezone.utc)
        conn.execute(
            """INSERT INTO memory_events
               (event_type, generation, headline, body, source_url, observed_at)
                VALUES (%s, %s, %s, %s, %s, %s)""",
            (event_type, generation, headline, body, source_url, now),
        )
        conn.commit()
        conn.close()
        logger.info(f"Memory event logged: {event_type} — {headline}")
    except Exception as e:
        logger.error(f"Memory event write error: {e}")


def write_memory_market_feed():
    """Write a lightweight JSON snapshot of current memory prices for the
    infra-metals-dashboard cron to pick up as a demand-side signal."""
    if not DATABASE_URL:
        return
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
