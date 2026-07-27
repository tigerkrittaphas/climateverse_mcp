"""Smoke tests for the ClimateVerse MCP server."""

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from climateverse_mcp import api
from climateverse_mcp.server import _assert_public_host, _normalize_doi, mcp
from climateverse_mcp.settings import Settings


@pytest.fixture
async def client(monkeypatch):
    # Hermetic settings: ignore the developer's real .env / env vars so the
    # missing-key path is exercised without touching the live API.
    monkeypatch.setattr(
        api,
        "get_settings",
        lambda: Settings(api_key="", api_base_url="", _env_file=None),
    )
    async with Client(mcp) as c:
        yield c


async def test_ping(client: Client):
    result = await client.call_tool("ping")
    assert result.data == "pong"


async def test_search_requires_api_key(client: Client):
    with pytest.raises(ToolError, match="CLIMATEVERSE_API_KEY"):
        await client.call_tool("search_datasets", {"query": "rainfall"})


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/data.csv",  # non-http scheme
        "http://localhost/admin",  # loopback
        "http://127.0.0.1:8080/",  # loopback IP
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://192.168.1.1/",  # private range
    ],
)
async def test_assert_public_host_blocks(url):
    with pytest.raises(ToolError):
        await _assert_public_host(url)


async def test_assert_public_host_allows_public():
    await _assert_public_host("https://india.climateverse.net/")


def test_normalize_doi():
    assert _normalize_doi("doi:10.71646/K9ZHZ7") == "doi:10.71646/K9ZHZ7"
    assert _normalize_doi("10.71646/K9ZHZ7") == "doi:10.71646/K9ZHZ7"
    assert _normalize_doi("https://doi.org/10.71646/K9ZHZ7") == "doi:10.71646/K9ZHZ7"


async def test_about_resource(client: Client):
    resources = await client.list_resources()
    assert any(str(r.uri) == "resource://about" for r in resources)
