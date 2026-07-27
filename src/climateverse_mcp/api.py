"""HTTP client for the ClimateVerse API, authenticated via CLIMATEVERSE_API_KEY."""

import httpx
from fastmcp.exceptions import ToolError

from climateverse_mcp.settings import get_settings


def api_client() -> httpx.AsyncClient:
    """Create an authenticated client for the ClimateVerse API.

    Raises a ToolError (surfaced to the MCP client) when no API key is set,
    so the user gets an actionable message instead of a bare 401.
    """
    settings = get_settings()
    if not settings.api_key:
        raise ToolError(
            "CLIMATEVERSE_API_KEY is not set. Add it to the `env` block of this "
            "server's MCP configuration (or a .env file next to the server)."
        )
    if not settings.api_base_url:
        raise ToolError("CLIMATEVERSE_API_BASE_URL is not set.")
    return httpx.AsyncClient(
        base_url=settings.api_base_url,
        headers={"X-Dataverse-key": settings.api_key},
        timeout=30.0,
    )
