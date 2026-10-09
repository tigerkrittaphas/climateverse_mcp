"""HTTP client for the ClimateVerse API.

Each call uses the first Dataverse API key it finds:

1. the `X-Dataverse-Key` header the MCP client sent with this request — how a
   user of the hosted server gets their own Dataverse permissions;
2. CLIMATEVERSE_API_KEY from the server's environment — how the local stdio
   server is configured;
3. none: anonymous, which sees published datasets only.
"""

import httpx
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_headers

from climateverse_mcp.settings import get_settings

# Same name as Dataverse's own API-key header. Lowercase: Starlette normalizes
# incoming header names.
DATAVERSE_KEY_HEADER = "x-dataverse-key"


def _api_key() -> str:
    """The Dataverse key for the current request, or "" for anonymous."""
    # Empty outside an HTTP request (stdio), so the environment key applies.
    sent = get_http_headers(include={DATAVERSE_KEY_HEADER}).get(DATAVERSE_KEY_HEADER)
    if sent and sent.strip():
        return sent.strip()
    return get_settings().api_key.strip()


async def _reject_bad_key(response: httpx.Response) -> None:
    # Dataverse answers 401 only for a key it does not accept; anonymous
    # requests for private data get 403/404 instead.
    if response.status_code == 401:
        raise ToolError(
            "Dataverse rejected the API key. Check the X-Dataverse-Key header "
            "(or CLIMATEVERSE_API_KEY) and that the token has not expired."
        )


def is_anonymous() -> bool:
    """True when the current request reaches Dataverse without any key."""
    return not _api_key()


def access_hint() -> str:
    """How to see more than this request can, for appending to errors."""
    if is_anonymous():
        return (
            " This server reads published datasets only; to see drafts or "
            "restricted files, send your Dataverse API key in the "
            "X-Dataverse-Key header (or set CLIMATEVERSE_API_KEY locally)."
        )
    return " Your Dataverse account may not have access to it."


def raise_for_dataverse_status(response: httpx.Response, what: str) -> None:
    """Turn Dataverse refusals into ToolErrors that say why and what to do."""
    if response.status_code == 404:
        raise ToolError(f"{what} not found or not visible.{access_hint()}")
    if response.status_code == 403:
        if "json" not in response.headers.get("content-type", ""):
            # Dataverse answers in JSON; an HTML 403 comes from the load
            # balancer in front of it, which rate-limits per client IP.
            raise ToolError(
                f"Dataverse at {response.url.host} refused the request "
                "(HTTP 403 from its load balancer, most likely its rate "
                "limit). Wait a few minutes before retrying."
            )
        raise ToolError(f"Access to {what} was denied.{access_hint()}")
    response.raise_for_status()


def api_client() -> httpx.AsyncClient:
    """Create a client for the ClimateVerse API, authenticated when a key is available."""
    settings = get_settings()
    if not settings.api_base_url:
        raise ToolError("CLIMATEVERSE_API_BASE_URL is not set.")
    key = _api_key()
    return httpx.AsyncClient(
        base_url=settings.api_base_url,
        headers={"X-Dataverse-key": key} if key else {},
        timeout=30.0,
        event_hooks={"response": [_reject_bad_key]},
    )
