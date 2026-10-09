"""HTTP client for the ClimateVerse API.

Authenticated with CLIMATEVERSE_API_KEY when set. Without a key, requests are
anonymous and see only published datasets, which is what the hosted server
uses: a Dataverse account with no roles would see exactly the same.
"""

import httpx
from fastmcp.exceptions import ToolError

from climateverse_mcp.settings import get_settings


def api_client() -> httpx.AsyncClient:
    """Create a client for the ClimateVerse API, authenticated when a key is set."""
    settings = get_settings()
    if not settings.api_base_url:
        raise ToolError("CLIMATEVERSE_API_BASE_URL is not set.")
    headers = {"X-Dataverse-key": settings.api_key} if settings.api_key else {}
    return httpx.AsyncClient(
        base_url=settings.api_base_url,
        headers=headers,
        timeout=30.0,
    )
