# Hardware Comps Dashboard — AI agent rules

## Stack

- FastAPI + Jinja2 server-rendered HTML. Not Next.js.
- Python 3.11+. `uv` for deps.
- Port from `$PORT`. CORS uses `endsWith` not `includes`.
- Dark mode by default. Design DNA: container queries, clamp() typography,
  micro-animations gated on `prefers-reduced-motion: no-preference`.

## Repo layout

```
app/
  main.py        — FastAPI app, routes, lifespan
  ebay_client.py — eBay Browse API OAuth2 client (from ebay-api-proxy, adapted)
  cache.py       — per-item price history + comps cache (SQLite, local)
  db_neon.py     — Neon multi-DB connection helper
  models.py      — Pydantic models
  proxy.py       — shared cross-app proxy client (from replit starter)
static/
  css/base.css   — design DNA, dark mode
  js/dashboard.js — table switching, sparklines, comp lookup UX
templates/
  base.html      — shared layout
  dashboard.html — main dashboard view
data/            — .gitignore'd; local SQLite cache + memory_market_feed.json
```

## Neon topology

One Neon project, single database, connected via `db_neon.py`. Env vars:

- `APP_NEON_URL` — Neon project base URL; individual DB URLs derived as
  `{APP_NEON_URL}/<dbname>` when possible, else set explicitly.

Databases:
- Everything in one DB via `DATABASE_URL`:
  - inventory + price_history
  - eBay comps cache (query-keyed, TTL)
  - DDR3/4/5 reference prices + supply-side events

## eBay

Embedded `ebay_client.py` (not via proxy). Env: `EBAY_CLIENT_ID`,
`EBAY_CLIENT_SECRET`. Cache is local SQLite in `data/comps_cache.db`
(7-day TTL for sold data, per query key).

## Auth

API routes protected by Bearer token in `Authorization` header, validated
against `HCD_API_TOKENS` env var (comma-separated). Health endpoint is public.

## Design rules

- Do NOT strip dark mode, container queries, clamp() typography, or
  `prefers-reduced-motion` gated animations.
- Static HTML is the deliverable — the dashboard IS the product, not a cron push.
- Price values in cents (integers) in DB; display in dollars in templates.
- Sparklines are inline SVG, generated server-side in templates (no charting lib).

## Commits

Conventional commits. One logical change per commit.
