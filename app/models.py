"""
Pydantic models for Hardware Comps Dashboard API.

All monetary values inbound are in dollars (float); stored in cents (int) in DB.
"""

from pydantic import BaseModel, Field
from typing import Any, Optional


class InventoryCreate(BaseModel):
    """Inbound inventory item (POST /api/inventory)."""
    id: Optional[int] = Field(None, description="Existing ID for updates; omit for create")
    table_type: str = Field(..., description="personal_assets | goodwill_flips | liquidation_pallets | ram_ssd_stockpile | device_farm")
    name: str = Field(..., description="Item name (e.g. Dell P2419H 24-inch monitor)")
    category: Optional[str] = None
    make: Optional[str] = None
    model: Optional[str] = None
    serial_number: Optional[str] = None
    condition: Optional[str] = None
    notes: Optional[str] = None
    acquisition_cost_cents: Optional[int] = None
    buy_source: Optional[str] = None
    ebay_active_avg_cents: Optional[int] = None
    ebay_sold_avg_cents: Optional[int] = None
    ebay_last_looked_at: Optional[str] = None
    search_query: Optional[str] = Field(None, description="eBay search query for comp lookups")
    metadata_json: Optional[dict[str, Any]] = None


class InventoryItem(BaseModel):
    """Outbound inventory item (GET /api/inventory)."""
    id: int
    table_type: str
    name: str
    category: Optional[str] = None
    make: Optional[str] = None
    model: Optional[str] = None
    serial_number: Optional[str] = None
    condition: Optional[str] = None
    notes: Optional[str] = None
    acquisition_cost_cents: Optional[int] = None
    buy_source: Optional[str] = None
    ebay_active_avg_cents: Optional[int] = None
    ebay_sold_avg_cents: Optional[int] = None
    ebay_last_looked_at: Optional[str] = None
    search_query: Optional[str] = None
    metadata_json: Optional[dict[str, Any]] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class CompsLookupRequest(BaseModel):
    """Inbound comp lookup request."""
    item_id: int = Field(..., description="Inventory item ID to attach the comp result to")
    query: str = Field(..., description="eBay search query")
    limit: int = Field(50, ge=1, le=200)
    aspects: Optional[dict[str, str]] = None
    table_type: str = Field("", description="Inventory table type (for cache key context)")


class CompsResult(BaseModel):
    """Outbound comp result."""
    active_items: list[dict] = []
    sold_items: list[dict] = []
    active_avg: float = 0.0
    sold_avg: float = 0.0
    active_count: int = 0
    sold_count: int = 0
    query: str = ""
    cache_key: str = ""
    fetched_at: float = 0.0
    query_text: str = ""


class PriceSnapshot(BaseModel):
    """Single price history snapshot."""
    id: Optional[int] = None
    item_id: int
    snapshot_at: Optional[str] = None
    sold_avg_cents: Optional[int] = None
    active_avg_cents: Optional[int] = None
    sold_count: Optional[int] = None
    active_count: Optional[int] = None
    comps_query: Optional[str] = None
    comps_json: Optional[dict] = None


class MemoryPrice(BaseModel):
    """DDR generation reference price row."""
    id: Optional[int] = None
    generation: str = Field(..., description="DDR3 | DDR4 | DDR5")
    form_factor: Optional[str] = None
    capacity_gb: Optional[int] = None
    speed_mhz: Optional[str] = None
    spot_avg_cents: Optional[int] = None
    source_query: Optional[str] = None
    observed_at: Optional[str] = None


class MemoryEvent(BaseModel):
    """Supply-side / price event."""
    id: Optional[int] = None
    event_type: str = Field(..., description="tariff | fab_capacity | price_spike | price_drop")
    generation: Optional[str] = None
    headline: str = ""
    body: Optional[str] = None
    source_url: Optional[str] = None
    observed_at: Optional[str] = None
