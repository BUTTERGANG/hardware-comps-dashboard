"""
Shared cross-app proxy client — from replit-starter-template.

Reads PROXY_API_URL + PROXY_API_TOKEN. Used for any future cross-app calls
(e.g., calling the shared eBay proxy instead of embedding — a later refactoring
option). Not used by the dashboard's embedded eBay client today.
"""

import os
import warnings
from typing import Any

import httpx

PROXY_API_URL = os.environ.get("PROXY_API_URL", "")
PROXY_API_TOKEN = os.environ.get("PROXY_API_TOKEN", "")


class ProxyClient:
    """Thin httpx wrapper for cross-app API calls."""

    def __init__(self, base_url: str = "", token: str = ""):
        self.base_url = base_url or PROXY_API_URL
        self.token = token or PROXY_API_TOKEN
        if not self.base_url:
            warnings.warn("PROXY_API_URL not set — proxy calls will fail")
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    async def get(self, path: str, **kwargs: Any) -> Any:
        resp = await self._client.get(path, **kwargs)
        resp.raise_for_status()
        return resp.json()

    async def post(self, path: str, **kwargs: Any) -> Any:
        resp = await self._client.post(path, **kwargs)
        resp.raise_for_status()
        return resp.json()

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "ProxyClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()


# Module-level singleton for simple cases
proxy = ProxyClient()
