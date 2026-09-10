"""
Claude API client for Hardware Comps Dashboard.

Two-call pipeline (mirrors thrift-lens pattern):
1. Vision call — identify item from photo → structured identification
2. Text call — identification + eBay comps → pricing analysis

Env:
  CLAUDE_API_KEY     — Anthropic API key (set when ready)
  CLAUDE_MODEL       — model to use (default claude-sonnet-4-20250514)
  CLAUDE_TIMEOUT_SEC — request timeout (default 60)
"""

import json
import logging
import os
import time
from typing import Any

import httpx

logger = logging.getLogger("hcd.claude")

CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY", "")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")
CLAUDE_TIMEOUT = int(os.environ.get("CLAUDE_TIMEOUT_SEC", "60"))
CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"

if not CLAUDE_API_KEY:
    logger.warning("CLAUDE_API_KEY unset — photo identification will 503 until set")


def _req_timeout() -> float:
    return max(10.0, float(CLAUDE_TIMEOUT))


class ClaudeClient:
    """Thin wrapper around the Anthropic Messages API."""

    def __init__(self):
        self._client = httpx.AsyncClient(
            base_url=CLAUDE_API_URL,
            headers={
                "X-API-Key": CLAUDE_API_KEY,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=_req_timeout(),
        )

    async def close(self):
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def chat(
        self,
        messages: list[dict[str, Any]],
        max_tokens: int = 4096,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Send a chat request. Returns parsed JSON body or raises."""
        model = model or CLAUDE_MODEL
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
            "temperature": 0.0,
        }
        resp = await self._client.post("/", json=payload)
        if resp.status_code != 200:
            text = resp.text[:500]
            raise RuntimeError(f"Claude API error {resp.status_code}: {text}")
        data = resp.json()
        return data


# Module-level helper — convenience for simple calls without context manager.
async def chat_sync(messages: list[dict[str, Any]], max_tokens: int = 4096, model: str | None = None) -> dict[str, Any]:
    """One-shot Claude chat (creates its own client, closes it)."""
    if not CLAUDE_API_KEY:
        raise RuntimeError("CLAUDE_API_KEY not configured")
    async with ClaudeClient() as client:
        return await client.chat(messages, max_tokens, model)


# ── Vision identification prompt ─────────────────────────────────────────────

VISION_SYSTEM_PROMPT = (
    "You are an expert appraiser of consumer electronics, computer hardware, "
    "and resale goods. Analyze the image carefully and identify the item shown. "
    "Return a JSON object with the fields below. Be specific — include model "
    "numbers, generation, capacity, speed, form factor, color, and any visible "
    "features that help pin down the exact product. If unsure about a field, "
    "set it to null rather than guessing."
)

VISION_USER_PROMPT = (
    "Identify the item shown in this image. Return ONLY a valid JSON object with "
    "these fields:\n\n"
    "{\n"
    '  "item_name": "specific item name (e.g. Dell P2419H 24-inch monitor)",\n'
    '  "brand": "brand name or null",\n'
    '  "model": "model number/series or null",\n'
    '  "category": "Category (e.g. Monitor, RAM, SSD, Camera, Laptop, Phone, Tablet, GPU, CPU, Console, Audio, Accessory, Other)",\n'
    '  "condition": "like_new | good | fair | poor | unknown",\n'
    '  "capacity_gb": 0,\n'
    '  "speed_mhz": "speed/rating string or null",\n'
    '  "form_factor": "SODIMM | DIMM | M.2 | 2.5" | U.2 | other or null",\n'
    '  "color": "color or null",\n'
    '  "era": "year range or null (e.g. 2018-2020)",\n'
    '  "ebay_search_query": "optimized eBay search query for comps (4-8 words, include brand + model + key spec)",\n'
    '  "confidence": "high | medium | low"\n'
    "}\n\n"
    "Do not include any text outside the JSON object. Do not add markdown formatting."
)


async def identify_from_image(image_bytes: bytes, image_mime: str = "image/jpeg") -> dict[str, Any]:
    """Send an image to Claude for item identification.

    Returns a dict shaped like:
        {item_name, brand, model, category, condition, capacity_gb,
         speed_mhz, form_factor, color, era, ebay_search_query, confidence}
    """
    if not CLAUDE_API_KEY:
        raise RuntimeError("CLAUDE_API_KEY not configured")

    b64 = image_bytes  # caller should base64-encode if needed; we accept raw bytes and encode here
    import base64
    b64_str = base64.b64encode(b64).decode("utf-8")

    msg = {
        "role": "user",
        "content": [
            {"type": "image", "source": {"type": "base64", "media_type": image_mime, "data": b64_str}},
            {"type": "text", "text": VISION_USER_PROMPT},
        ],
    }
    data = await chat_sync(
        messages=[{"role": "system", "content": VISION_SYSTEM_PROMPT}, msg],
        max_tokens=2048,
    )
    # Parse the text content out of the response
    text = ""
    for block in data.get("content", []):
        if block.get("type") == "text":
            text = block.get("text", "")
            break
    # Try to parse JSON from the text; Claude sometimes wraps it in markdown
    import re
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
       text = m.group(0)
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        logger.error(f"Claude returned non-JSON identification: {text[:300]}")
        return {
            "item_name": "Unknown",
            "brand": None,
            "model": None,
            "category": "Other",
            "condition": "unknown",
            "capacity_gb": 0,
            "speed_mhz": None,
            "form_factor": None,
            "color": None,
            "era": None,
            "ebay_search_query": text[:200],
            "confidence": "low",
            "_raw": text[:1000],
        }
    # Normalize
    result.setdefault("item_name", "Unknown")
    result.setdefault("confidence", "low")
    result.setdefault("category", "Other")
    result.setdefault("condition", "unknown")
    return result


# ── Text analysis prompt (identification + comps → pricing) ──────────────────

TEXT_SYSTEM_PROMPT = (
    "You are an expert resale pricing analyst. Given an item identification and "
    "real eBay comp data (active listings + sold listings with prices), produce "
    "a pricing analysis for resale. Return a JSON object with the fields below. "
    "All monetary values are in USD dollars (not cents)."
)

TEXT_USER_PROMPT_TEMPLATE = (
    "Identify the item: {identification}\n\n"
    "eBay active listings ({active_count} shown, avg ${active_avg:.2f}): {active_comps}\n\n"
    "eBay sold listings ({sold_count} shown, avg ${sold_avg:.2f}): {sold_comps}\n\n"
    "Produce a pricing analysis. Return ONLY a JSON object:\n\n"
    "{\n"
    '  "market_value_low": 0.0,\n'
    '  "market_value_high": 0.0,\n'
    '  "suggested_list_price": 0.0,\n'
    '  "profit_estimate_low": 0.0,\n'
    '  "profit_estimate_high": 0.0,\n'
    '  "deal_score": "HOT | GOOD | PASS | UNKNOWN",\n'
    '  "deal_score_reason": "one sentence",\n'
    '  "best_platforms": ["eBay", "Facebook Marketplace", "..."],\n'
    '  "selling_tips": ["tip 1", "..."],\n'
    '  "keywords_for_listing": ["keyword1", "..."],\n'
    '  "watch_out_for": "risk to watch for",\n'
    '  "data_confidence": "high | medium | low"\n'
    "}\n\n"
    "Do not include any text outside the JSON object."
)


async def analyze_item(
    identification: dict[str, Any],
    active_comps: list[dict[str, Any]],
    sold_comps: list[dict[str, Any]],
) -> dict[str, Any]:
    """Produce a pricing analysis given an identification + eBay comps."""
    if not CLAUDE_API_KEY:
        raise RuntimeError("CLAUDE_API_KEY not configured")

    active_avg = sum(c.get("price", 0) for c in active_comps) / len(active_comps) if active_comps else 0
    sold_avg = sum(c.get("price", 0) for c in sold_comps) / len(sold_comps) if sold_comps else 0

    active_list = "\n".join(
        f"  - {c.get('title','?')}: ${c.get('price',0):.2f} ({c.get('condition','?')})" for c in active_comps[:10]
    ) or "  (none)"
    sold_list = "\n".join(
        f"  - {c.get('title','?')}: ${c.get('price',0):.2f} ({c.get('condition','?')})" for c in sold_comps[:10]
    ) or "  (none)"

    prompt = TEXT_USER_PROMPT_TEMPLATE.format(
        identification=json.dumps(identification, indent=2),
        active_count=len(active_comps),
        active_avg=active_avg,
        active_comps=active_list,
        sold_count=len(sold_comps),
        sold_avg=sold_avg,
        sold_comps=sold_list,
    )

    data = await chat_sync(
        messages=[
            {"role": "system", "content": TEXT_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        max_tokens=2048,
    )

    text = ""
    for block in data.get("content", []):
        if block.get("type") == "text":
            text = block.get("text", "")
            break
    import re
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        text = m.group(0)
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        logger.error(f"Claude returned non-JSON analysis: {text[:300]}")
        return {
            "market_value_low": 0,
            "market_value_high": 0,
            "suggested_list_price": 0,
            "profit_estimate_low": 0,
            "profit_estimate_high": 0,
            "deal_score": "UNKNOWN",
            "deal_score_reason": "Analysis failed — Claude returned non-JSON",
            "best_platforms": ["eBay"],
            "selling_tips": [],
            "keywords_for_listing": [],
            "watch_out_for": "",
            "data_confidence": "low",
            "_raw": text[:1000],
        }
    result.setdefault("deal_score", "UNKNOWN")
    result.setdefault("data_confidence", "low")
    return result


# ── Query refinement prompt ───────────────────────────────────────────────────

REFINE_QUERY_SYSTEM_PROMPT = (
    "You are an expert at crafting precise eBay search queries for resale comp research. "
    "Given an item identification and the first round of eBay comp results, propose an improved "
    "search query that will surface more relevant comparable listings. The better the query, the "
    "more accurate the comp averages. Return ONLY a short query string (3-10 words), no explanation."
)

REFINE_QUERY_USER_PROMPT_TEMPLATE = (
    "Item: {identification}\n\n"
    "First-pass eBay query: {first_query}\n\n"
    "First-pass results — active: {active_count} items (avg ${active_avg:.2f}), "
    "sold: {sold_count} items (avg ${sold_avg:.2f})\n\n"
    "Active listings seen:\n{active_list}\n\n"
    "Sold listings seen:\n{sold_list}\n\n"
    "Propose an improved eBay search query that will find better comps for this item. "
    "Return ONLY the query string.\n"
)


async def refine_query(
    identification: dict[str, Any],
    first_query: str,
    active_comps: list[dict[str, Any]],
    sold_comps: list[dict[str, Any]],
) -> str:
    """Run a second-pass Claude call to refine the eBay search query."""
    if not CLAUDE_API_KEY:
        raise RuntimeError("CLAUDE_API_KEY not configured")

    active_avg = sum(c.get("price", 0) for c in active_comps) / len(active_comps) if active_comps else 0
    sold_avg = sum(c.get("price", 0) for c in sold_comps) / len(sold_comps) if sold_comps else 0

    active_list = "\n".join(
        f"  - {c.get('title','?')}: ${c.get('price',0):.2f} ({c.get('condition','?')})"
        for c in active_comps[:8]
    ) or "  (none)"
    sold_list = "\n".join(
        f"  - {c.get('title','?')}: ${c.get('price',0):.2f} ({c.get('condition','?')})"
        for c in sold_comps[:8]
    ) or "  (none)"

    prompt = REFINE_QUERY_USER_PROMPT_TEMPLATE.format(
        identification=json.dumps(identification, indent=2),
        first_query=first_query,
        active_count=len(active_comps),
        active_avg=active_avg,
        active_list=active_list,
        sold_count=len(sold_comps),
        sold_avg=sold_avg,
        sold_list=sold_list,
    )

    data = await chat_sync(
        messages=[
            {"role": "system", "content": REFINE_QUERY_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        max_tokens=256,
    )

    text = ""
    for block in data.get("content", []):
        if block.get("type") == "text":
            text = block.get("text", "")
            break

    # Clean up: take the first non-empty line as the refined query
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if lines:
        # Remove markdown fences if present
        refined = lines[0]
        for prefix in ["```", "`", '"', "'"]:
            if refined.startswith(prefix):
                refined = refined[len(prefix):]
            if refined.endswith(prefix):
                refined = refined[:-len(prefix)]
        refined = refined.strip()
        if refined and len(refined) >= 3:
            return refined

    logger.warning(f"Query refinement returned unparseable result: {text[:200]}")
    return first_query  # fall back to first query


# ── Convenience: identify + comps in one pipeline ─────────────────────────────

async def identify_and_comps(
    image_bytes: bytes,
    image_mime: str = "image/jpeg",
    ebay_client=None,
    comps_limit: int = 20,
    do_refinement: bool = True,
) -> dict[str, Any]:
    """Full pipeline: photo → identify → eBay comps → (optional refine query → second comps) → pricing analysis.

    Returns dict with:
      - identification: {item_name, brand, model, category, condition, ...}
      - comps: {active_items, sold_items, active_avg, sold_avg, ...}
      - analysis: {market_value_low, market_value_high, deal_score, ...}
      - refined_query: the query used for the final comps (after optional refinement)
    """
    if ebay_client is None:
        from app.ebay_client import eBayClient
        ebay_client = eBayClient()

    identification = await identify_from_image(image_bytes, image_mime)
    first_query = identification.get("ebay_search_query") or identification.get("item_name", "")
    if not first_query:
        first_query = "electronics"

    # First-pass comps with the requested limit
    comps = await ebay_client.lookup_comps(first_query, comps_limit)

    refined_query = first_query
    if do_refinement:
        try:
            refined_query = await refine_query(
                identification,
                first_query,
                comps.get("active_items", []),
                comps.get("sold_items", []),
            )
        except Exception as e:
            logger.warning(f"Query refinement failed, using first query: {e}")
            refined_query = first_query

        # Second-pass comps with refined query, same limit
        if refined_query != first_query:
            comps = await ebay_client.lookup_comps(refined_query, comps_limit)

    analysis = await analyze_item(
        identification,
        comps.get("active_items", []),
        comps.get("sold_items", []),
    )

    return {
        "identification": identification,
        "comps": comps,
        "analysis": analysis,
        "refined_query": refined_query,
    }
