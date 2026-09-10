# Hardware Comps Dashboard — Search Feature Spec

**Date:** 2026-09-10  
**Status:** Built — matches implementation  
**Scope:** Text search → eBay comps + Photo upload → Claude Vision identify → eBay comps → query refinement → pricing analysis

---

## 1. What we're building

Two new search features on the existing dashboard, both wired to the same eBay client and (for photo) Claude API:

| Feature | Flow | Key dependency |
|---|---|---|
| **Text search** | Type query → eBay active+sold comps → inline results + save-as-item | eBay client (existing) |
| **Photo search** | Upload image → Claude Vision identify → eBay comps on identified item → pricing analysis → save-as-item | Claude API + eBay client |

Both share: same comp results panel, same "save as inventory item" action, same eBay caching.

---

## 2. Text search → eBay comps

### 2.1 UI placement
- New panel appears above the inventory table on every dashboard page.
- Two tabs: **Text search** | **Photo search** — same panel, toggle between modes.
- Text search tab: one input field + search button + limit selector (1–200, default 50).
- Results appear inline below the search bar (no page navigation, no modal).

### 2.2 Backend endpoint

```
GET /api/search?q=<query>&limit=<n>
Auth: Bearer token required
Returns:
  {
    "query": "Samsung 8GB DDR4 SODIMM",
    "active_items": [...],      // eBay active listings (EbayComp shape)
    "sold_items": [...],        // eBay sold listings
    "active_avg": 45.00,        // mean active price in dollars
    "sold_avg": 38.50,          // mean sold price in dollars
    "active_count": 12,
    "sold_count": 8,
    "suggested_query": "..."    // normalized query used
  }
```

Reuses `ebay.lookup_comps(query, limit)` from `app/ebay_client.py` — which already runs active + sold searches and returns both averages. No new eBay logic.

### 2.3 Result panel UI

Shows:
- Query echo + comp counts ("24 comps — sold avg $38.50, active avg $45.00")
- Two summary cards: Sold avg, Active avg (big numbers)
- Scrollable list of comp items — up to 40 shown, each with: thumbnail, title, condition, listing type, price
- "Save as inventory item" row: cost input ($), table type dropdown, Save button

### 2.4 Save-as-item action

POSTs to existing `POST /api/inventory` with:
- `name` = search query
- `table_type` = user-selected (default goodwill_flips)
- `search_query` = suggested_query from comps result
- `ebay_active_avg_cents`, `ebay_sold_avg_cents` = from comps result
- `acquisition_cost_cents` = user-entered cost
- `buy_source` = "eBay search"

Then refreshes the current table view if the saved table matches the active tab. User can edit details later.

---

## 3. Photo search → identify → comps → analysis

### 3.1 UI placement
- Same search panel as text search, "Photo search" tab.
- Photo upload area: dashed-border drop zone with click-to-browse, drag-and-drop, preview on select.
- File constraints: JPEG/PNG/WEBP, max 10MB (matches thrift-lens limit).
- After upload: image preview replaces placeholder, "Identify + search comps" button enables.
- Results appear inline below the upload area (same panel, no modal).

### 3.2 Backend endpoints

**3.2.1 Photo identification + comps**

```
POST /api/search/photo
Auth: Bearer token required
Body: { "image_b64": "<base64>", "image_mime": "image/jpeg", "limit": 20 }
Returns:
  {
    "identification": {
      "item_name": "Samsung 8GB DDR4-2666 SODIMM",
      "brand": "Samsung",
      "model": null,
      "category": "RAM",
      "condition": "good",
      "capacity_gb": 8,
      "speed_mhz": "2666",
      "form_factor": "SODIMM",
      "color": null,
      "era": "2018-2020",
      "ebay_search_query": "Samsung 8GB DDR4-2666 SODIMM",
      "confidence": "high"
    },
    "comps": { ...same shape as /api/search... },
    "analysis": {
      "market_value_low": 30.00,
      "market_value_high": 42.00,
      "suggested_list_price": 36.00,
      "profit_estimate_low": 0.00,
      "profit_estimate_high": 12.00,
      "deal_score": "GOOD",
      "deal_score_reason": "...",
      "best_platforms": ["eBay", "Facebook Marketplace"],
      "selling_tips": ["Clean contacts before listing", "..."],
      "keywords_for_listing": ["Samsung", "8GB", "DDR4", "SODIMM", "..."],
      "watch_out_for": "...",
      "data_confidence": "high"
    },
    "refined_query": "Samsung 8GB DDR4-2666 SODIMM"   // query used for final comps call (after optional refinement)
  }
```

Flow:
1. Receive base64 image + mime type
2. Validate: non-empty, valid base64, under 10MB
3. Claude Vision call → structured identification JSON
4. Extract `ebay_search_query` from identification (fall back to `item_name`)
5. First-pass eBay comps call using that query
6. Query refinement call (third Claude call) → refined query
7. Second-pass eBay comps call with refined query (only if it changed)
8. Claude text analysis call (second Claude call) → pricing + deal score (identification + comps as input)
9. Return combined result (including `refined_query`)

**3.2.2 Save identification as item**

```
POST /api/search/save-identification
Auth: Bearer token required
Body: { "identification": {...}, "comps": {...}, "analysis": {...},
        "table_type": "goodwill_flips", "acquisition_cost_cents": 500, "buy_source": "Photo scan" }
Returns: { "ok": true, "id": 123, "item": {...} }
```

Creates inventory item from identification fields (name, make, model, category, condition, search_query) + comp averages + user cost + metadata_json holding the full identification/comps/analysis for audit trail. Writes a price_history snapshot. Same upsert + snapshot pattern as the comp lookup flow.

### 3.3 Claude integration

New file: `app/claude_client.py` — thin wrapper around Anthropic Messages API.

Env vars:
- `CLAUDE_API_KEY` — required for photo search
- `CLAUDE_MODEL` — default `claude-sonnet-5`
- `CLAUDE_TIMEOUT_SEC` — default 60

Two structured calls, mirroring the thrift-lens two-call pattern. Photo search also runs a third call (query refinement) after the first-pass eBay comps.

**Call 1 — Vision identification (system + image):**
- System: "You are an expert appraiser of consumer electronics, computer hardware, and resale goods."
- User: image + prompt asking for JSON with fields: `item_name`, `brand`, `model`, `category`, `condition`, `capacity_gb`, `speed_mhz`, `form_factor`, `color`, `era`, `ebay_search_query`, `confidence`
- Output parsed as JSON (falls back to raw text if Claude returns non-JSON)

**Call 2 — Text analysis (text only, no image):**
- System: "You are an expert resale pricing analyst."
- User: identification JSON + active comps list (title/price/condition) + sold comps list + averages
- Output: pricing analysis JSON with `market_value_low/high`, `suggested_list_price`, `profit_estimate_low/high`, `deal_score`, `deal_score_reason`, `best_platforms`, `selling_tips`, `keywords_for_listing`, `watch_out_for`, `data_confidence`

The photo-search endpoint orchestrates both calls + the eBay call into one request/response cycle. Timeout budget: ~45s for the full pipeline (Vision ~10s, first-pass eBay ~2s, query refinement ~10s, second-pass eBay ~2s if query changed, text analysis ~10s).

**Graceful degradation:** If Claude API key is missing, photo search returns 503 with clear message. If eBay keys are missing, both text and photo search return 503. If Claude returns non-JSON, we still return the raw text in `_raw` field and treat confidence as "low" — the UI still shows something useful.

### 3.4 Photo result panel UI

Shows (in order):
- **Identification block:** Item name (big), brand/model, category, condition, confidence badge
- **Comps summary cards:** Total comps count, Sold avg, Active avg
- **Comps list:** Same scrollable list as text search (top 20)
- **Analysis block:** Deal score badge (HOT/GOOD/PASS/UNKNOWN), value range, suggested list price, platforms, tips list
- **Save-as-item row:** Same as text search — cost input, table type dropdown, Save button

---

## 4. Reuse map

| Existing piece | Used for |
|---|---|
| `app/ebay_client.py` → `eBayClient.lookup_comps()` | Both text and photo search comps |
| `app/db_neon.py` → `get_conn()`, `upsert_inventory_db()`, `write_price_snapshot()` | Save-as-item, price history write |
| `app/models.py` → `InventoryCreate` | Save-as-item input validation |
| `static/css/base.css` → existing design DNA | New search panel styles follow same variables |
| `static/js/dashboard.js` → existing fetch/auth/helpers | New search panel JS reuses `fetchJSON()`, `toast()`, `fmtMoney()`, auth |
| thrift-lens vision pipeline | Pattern reference for two-call Claude flow (vision ID → text analysis) |
| gear-rental eBay lookup pattern | Reference for active+sold avg → item valuation |

---

## 5. New files

| File | Purpose |
|---|---|
| `app/claude_client.py` | Claude API wrapper: vision identification + text analysis calls, plus `identify_and_comps()` orchestration |
| Template additions in `templates/dashboard.html` | Search panel (text + photo tabs, upload zone, results panels, save-as-item row) |
| JS additions in `static/js/dashboard.js` | `wireSearchPanel()`, `doTextSearch()`, `doPhotoSearch()`, `renderTextSearchResult()`, `renderPhotoSearchResult()`, `saveSearchResultAsItem()`, photo drag/drop + preview |
| CSS additions in `static/css/base.css` | `.search-panel`, `.search-tab`, `.search-result`, comp item list styles, photo upload zone, deal-score badges, analysis block |

Files NOT needed: no new DB tables, no new Neon schema, no migration. The inventory table already handles item storage; price_history already handles trend snapshots; metadata_json already holds arbitrary structured data.

---

## 6. API routes summary (new)

| Method | Path | Auth | Body/Params | Returns |
|---|---|---|---|---|
|| GET | `/api/search` | Bearer | `q` (required), `limit` (default 20) | Comps + averages |
|| POST | `/api/search/photo` | Bearer | `{image_b64, image_mime, limit}` (limit default 20) | identification + comps + analysis + refined_query |
|| POST | `/api/search/save-identification` | Bearer | `{identification, comps, analysis, table_type, acquisition_cost_cents, ...}` | saved item |

---

## 7. What's NOT in scope for this pass

- Amazon comps / Keepa integration (doc says v2, not launch-blocker)
- User accounts / multi-user auth (dashboard is single-user with API token today)
- Photo storage / image history (images sent to Claude and discarded, same as thrift-lens)
- OCR for pallet manifests (separate feature, not this)
- Device farm Appium integration (separate feature, not this)
- Memory market DDR price ingestion automation (manual event logging only for now; the `memory_events` table exists, the UI has the "Log event" button)

---

## 8. Open questions for you

1. **Claude model choice** — default `claude-sonnet-4-20250514` in the spec. Want a different model (haiku for speed, opus for accuracy on identification)?
2. **Photo search result detail** — the spec shows top 20 comps in the photo result panel. Want more/fewer?
3. **Save-as-item default table** — spec defaults to `goodwill_flips` for both text and photo save. Want text search to default to a different table, or remember the last-used table?
4. **eBay query optimization** — the Claude vision prompt produces an `ebay_search_query` field. Do you want the photo flow to use that directly, or run a second refinement pass?

Once you sign off on the spec, I'll build it in one pass: claude_client.py + route wiring + template additions + JS + CSS, then verify with a server boot test.
