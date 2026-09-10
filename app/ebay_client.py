"""
eBay Browse API client — embedded (not via proxy).

Adapted from ebay-api-proxy/ebay_client.py. Same OAuth2 client-credentials
flow, same search_active_items / search_sold_items shape. Adds lookup_comps()
which returns both active and sold averages in one call (gear-rental pattern).
"""

import logging
import os
import time
from urllib.parse import quote

import httpx

logger = logging.getLogger("hcd.ebay")

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
BROWSE_URL = "https://api.ebay.com/buy/browse/v1"
TAXONOMY_URL = "https://api.ebay.com/commerce/taxonomy/v1"

ASPECT_FILTER_MAP = {
    "brand": "Brand",
    "size": "Size",
    "size_type": "Size Type",
    "color": "Color",
    "condition": "Condition",
}

EBAY_CLIENT_ID = os.environ.get("EBAY_CLIENT_ID", "")
EBAY_CLIENT_SECRET = os.environ.get("EBAY_CLIENT_SECRET", "")

_token_cache = {"access_token": None, "expires_at": 0}


def _get_creds():
    if not EBAY_CLIENT_ID or not EBAY_CLIENT_SECRET:
        raise RuntimeError("EBAY_CLIENT_ID and EBAY_CLIENT_SECRET must be set")
    return EBAY_CLIENT_ID, EBAY_CLIENT_SECRET


async def get_access_token(force_refresh=False):
    now = time.time()
    cached = _token_cache.get("access_token")
    if cached and _token_cache["expires_at"] > now + 60 and not force_refresh:
        return cached
    cid, secret = _get_creds()
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "scope": "https://api.ebay.com/oauth/api_scope",
            },
            auth=(cid, secret),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
    if resp.status_code != 200:
        raise RuntimeError(f"OAuth2 failed: {resp.status_code} {resp.text[:300]}")
    body = resp.json()
    _token_cache["access_token"] = body["access_token"]
    _token_cache["expires_at"] = now + body.get("expires_in", 7200)
    logger.info("eBay OAuth2 token refreshed")
    return _token_cache["access_token"]


def _build_aspect_filter(aspects: dict) -> str:
    if not aspects:
        return ""
    parts = []
    for key, val in aspects.items():
        if not val:
            continue
        aspect_name = ASPECT_FILTER_MAP.get(key, key)
        safe_val = str(val).replace(",", "\\,").replace(":", "\\:")
        parts.append(
            f"{{aspectName:{aspect_name},aspectFilterValues:[{{value:{safe_val}}}]}}"
        )
    if not parts:
        return ""
    return f"aspectFilter={','.join(parts)}"


async def search_active_items(query: str, limit: int = 50, aspects: dict | None = None, category_ids: str | None = None):
    token = await get_access_token()
    params = [("q", query), ("limit", str(min(limit, 200)))]
    if category_ids:
        params.append(("category_ids", category_ids))
    filters = []
    if aspects:
        af = _build_aspect_filter(aspects)
        if af:
            filters.append(af)
    if filters:
        params.append(("filter", ",".join(filters)))
    url = f"{BROWSE_URL}/item_search"
    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
    }
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, params=params, headers=headers, timeout=20)
    if resp.status_code != 200:
        logger.error(f"eBay active search failed: {resp.status_code} {resp.text[:400]}")
        return {"items": [], "total": 0, "error": resp.text[:500]}
    data = resp.json()
    items = []
    for item in data.get("itemSummaries", []):
        price = item.get("price", {})
        items.append({
            "item_id": item.get("itemId"),
            "title": item.get("title"),
            "price": float(price.get("value", 0)) if price.get("value") else None,
            "currency": price.get("currency"),
            "condition": item.get("condition"),
            "condition_id": item.get("conditionId"),
            "item_url": item.get("itemWebUrl"),
            "image_url": (
                (item.get("image", {}) or {}).get("imageUrl")
                or (item.get("thumbnailImages") or [{}])[0].get("imageUrl")
                or ""
            ),
            "seller": (item.get("seller", {}) or {}).get("username"),
            "shipping_cost": float(
                (item.get("shippingOptions") or [{}])[0]
                .get("shippingCost", {})
                .get("value", 0)
            ) if item.get("shippingOptions") else None,
            "item_location": item.get("itemLocation", {}).get("addressLocality"),
            "listing_type": item.get("listingType"),
            "bid_count": item.get("bidCount"),
            "buying_options": item.get("buyingOptions", []),
        })
    return {"items": items, "total": data.get("total", len(items))}


async def search_sold_items(query: str, limit: int = 50, aspects: dict | None = None, category_ids: str | None = None):
    token = await get_access_token()
    params = [("q", query), ("limit", str(min(limit, 200)))]
    if category_ids:
        params.append(("category_ids", category_ids))
    filters = ["soldItemsOnly:{true}"]
    if aspects:
        af = _build_aspect_filter(aspects)
        if af:
            filters.append(af)
    params.append(("filter", ",".join(filters)))
    url = f"{BROWSE_URL}/item_search"
    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
    }
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, params=params, headers=headers, timeout=20)
    if resp.status_code != 200:
        logger.error(f"eBay sold search failed: {resp.status_code} {resp.text[:400]}")
        return {"items": [], "total": 0, "error": resp.text[:500]}
    data = resp.json()
    items = []
    for item in data.get("itemSummaries", []):
        price = item.get("price", {})
        items.append({
            "item_id": item.get("itemId"),
            "title": item.get("title"),
            "price": float(price.get("value", 0)) if price.get("value") else None,
            "currency": price.get("currency"),
            "condition": item.get("condition"),
            "condition_id": item.get("conditionId"),
            "item_url": item.get("itemWebUrl"),
            "image_url": (
                (item.get("image", {}) or {}).get("imageUrl")
                or (item.get("thumbnailImages") or [{}])[0].get("imageUrl")
                or ""
            ),
            "seller": (item.get("seller", {}) or {}).get("username"),
            "shipping_cost": float(
                (item.get("shippingOptions") or [{}])[0]
                .get("shippingCost", {})
                .get("value", 0)
            ) if item.get("shippingOptions") else None,
            "listing_type": item.get("listingType"),
            "bid_count": item.get("bidCount"),
        })
    return {"items": items, "total": data.get("total", len(items))}


async def get_category_id(query: str):
    token = await get_access_token()
    url = f"{TAXONOMY_URL}/category_tree/v1/get_category_suggestions"
    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
    }
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, params={"q": query}, headers=headers, timeout=15)
    if resp.status_code != 200:
        return None, None
    data = resp.json()
    suggestions = data.get("categorySuggestions", [])
    if not suggestions:
        return None, None
    best = suggestions[0].get("category", {})
    return best.get("categoryId"), best.get("categoryName")


class eBayClient:
    """Convenience wrapper: lookup_comps returns active + sold averages."""

    async def lookup_comps(self, query: str, limit: int = 50, aspects: dict | None = None, table_type: str = ""):
        """Run active + sold eBay searches, return comps + averages.

        Returns dict with:
          - "active_items": list of active listing dicts
          - "sold_items": list of sold listing dicts
          - "active_avg": mean active price (float, dollars)
          - "sold_avg": mean sold price (float, dollars)
          - "query": normalized query string
          - "cache_key": sha256 key for local cache
          - "fetched_at": ISO timestamp
        """
        active = await search_active_items(query, limit, aspects)
        sold = await search_sold_items(query, limit, aspects)

        active_prices = [i["price"] for i in active.get("items", []) if i.get("price")]
        sold_prices = [i["price"] for i in sold.get("items", []) if i.get("price")]

        active_avg = round(sum(active_prices) / len(active_prices), 2) if active_prices else 0
        sold_avg = round(sum(sold_prices) / len(sold_prices), 2) if sold_prices else 0

        import hashlib
        normalized = " ".join(query.lower().split())
        cache_key = hashlib.sha256(normalized.encode()).hexdigest()[:32]

        return {
            "active_items": active.get("items", []),
            "sold_items": sold.get("items", []),
            "active_avg": active_avg,
            "sold_avg": sold_avg,
            "active_count": len(active_prices),
            "sold_count": len(sold_prices),
            "query": normalized,
            "cache_key": cache_key,
            "fetched_at": time.time(),
            "query_text": query,
        }
