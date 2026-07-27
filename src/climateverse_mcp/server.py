"""ClimateVerse MCP server."""

import sys

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

import asyncio
import ipaddress
import json
import re
import socket

import httpx

from .api import api_client
from .instruction import EDA_INSTRUCTIONS, INSTRUCTIONS
from .settings import get_settings

mcp = FastMCP(
    name="climateverse-mcp",
    instructions=INSTRUCTIONS,
)


@mcp.tool
def ping() -> str:
    """Health check — returns 'pong' if the server is alive."""
    return "pong"


async def _search(query: str, limit: int) -> dict:
    """Query the Dataverse Search API, paginating until `limit` datasets."""
    per_page = 100
    datasets: list[dict] = []
    start = 0
    total: int | None = None

    async with api_client() as client:
        while (total is None or start < total) and len(datasets) < limit:
            response = await client.get(
                "/api/search",
                params={
                    "q": query,
                    "type": "dataset",
                    "per_page": min(per_page, limit - len(datasets)),
                    "start": start,
                    "sort": "name",
                    "order": "asc",
                },
            )
            response.raise_for_status()
            data = response.json()["data"]
            total = int(data["total_count"])
            items = data.get("items") or []
            if not items:
                break
            datasets.extend(
                {
                    "doi": item.get("global_id"),
                    "title": item.get("name"),
                    "description": item.get("description"),
                    "url": item.get("url"),
                    "published_at": item.get("published_at"),
                    "file_count": item.get("fileCount"),
                }
                for item in items
            )
            start += len(items)

    return {"total_count": total, "returned": len(datasets), "datasets": datasets}


@mcp.tool
async def list_datasets(limit: int = 100) -> dict:
    """List datasets in the ClimateVerse catalog.

    Returns compact records (doi, title, description, url) plus the catalog's
    total_count. Use `limit` to page through more.
    """
    return await _search("*", limit)


@mcp.tool
async def search_datasets(query: str, limit: int = 10) -> dict:
    """Search the ClimateVerse catalog for datasets matching a query."""
    return await _search(query, limit)


def _normalize_doi(doi: str) -> str:
    """Accept 'doi:10.x/Y', '10.x/Y', or a doi.org URL; return 'doi:10.x/Y'."""
    doi = doi.strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi.org/"):
        if doi.startswith(prefix):
            doi = doi[len(prefix) :]
    if not doi.startswith("doi:"):
        doi = f"doi:{doi}"
    return doi

def _unwrap_field(value):
    """Normalize Dataverse metadata field values into plain Python values."""
    if isinstance(value, list):
        return [_unwrap_field(v) for v in value]
    if isinstance(value, dict):
        if "typeName" in value and "value" in value:
            return _unwrap_field(value["value"])
        return {k: _unwrap_field(v) for k, v in value.items()}
    return value


async def _fetch_dataset_meta(doi: str) -> dict:
    """Fetch dataset metadata from the Native API's latest-version endpoint."""
    persistent_id = _normalize_doi(doi)
    async with api_client() as client:
        response = await client.get(
            "/api/datasets/:persistentId/versions/:latest",
            params={"persistentId": persistent_id},
        )
        if response.status_code == 404:
            raise ToolError(f"Dataset not found: {persistent_id}")
        response.raise_for_status()
        data = response.json()["data"]

    # Flatten metadataBlocks into {typeName: value}.
    fields: dict = {}
    for block in data.get("metadataBlocks", {}).values():
        for field in block.get("fields", []):
            fields[field["typeName"]] = _unwrap_field(field.get("value"))

    descriptions = fields.get("dsDescription") or []
    description = None
    if descriptions and isinstance(descriptions[0], dict):
        description = descriptions[0].get("dsDescriptionValue")

    return {
        "doi": data.get("datasetPersistentId"),
        "title": fields.get("title"),
        "description": description,
        "subjects": fields.get("subject"),
        "authors": fields.get("author"),
        "time_period_covered": fields.get("timePeriodCovered"),
        "access_to_sources": fields.get("accessToSources"),
        "version_state": data.get("versionState"),
        "publication_date": data.get("publicationDate"),
        "license": (data.get("license") or {}).get("name"),
        "files": [f.get("label") for f in data.get("files", [])],
    }


async def _fetch_codebook(doi: str) -> str:
    """Download codebook.md from a dataset's latest version."""
    persistent_id = _normalize_doi(doi)
    async with api_client() as client:
        # Native API: list files in the latest dataset version. Because the
        # request is authenticated (X-Dataverse-key), ':latest' resolves to the
        # DRAFT version when one exists — so unpublished codebooks are visible
        # during development — and falls back to the latest published version.
        response = await client.get(
            "/api/datasets/:persistentId/versions/:latest/files",
            params={"persistentId": persistent_id},
        )
        if response.status_code == 404:
            raise ToolError(f"Dataset not found: {persistent_id}")
        response.raise_for_status()
        files = response.json()["data"]

        codebook = next(
            (
                f
                for f in files
                if (f.get("label") or f.get("dataFile", {}).get("filename", ""))
                .lower()
                == "codebook.md"
            ),
            None,
        )
        if codebook is None:
            labels = [f.get("label") for f in files]
            raise ToolError(
                f"No codebook.md in {persistent_id}. Files present: {labels}"
            )

        # Access API: download the file content by its id.
        file_id = codebook["dataFile"]["id"]
        content = await client.get(f"/api/access/datafile/{file_id}")
        content.raise_for_status()
        return content.text


@mcp.tool
async def describe_dataset(doi: str) -> dict:
    """Describe a dataset's metadata: title, description, authors, coverage,
    license, and file list.

    `doi` accepts 'doi:10.71646/XXXXX', '10.71646/XXXXX', or a doi.org URL.
    """
    return await _fetch_dataset_meta(doi)


@mcp.tool
async def get_codebook(doi: str) -> str:
    """Retrieve the codebook.md of a dataset — THE primary artifact describing
    how to acquire, parse, and analyze its data.

    `doi` accepts 'doi:10.71646/XXXXX', '10.71646/XXXXX', or a doi.org URL.
    """
    return await _fetch_codebook(doi)


MAX_SAMPLE_BYTES = 262_144  # hard cap so tool results stay context-safe
_REDIRECT_LIMIT = 3
_DNS_TIMEOUT = 5.0  # bound the DNS lookup (blocking getaddrinfo has no timeout)
_OVERALL_TIMEOUT = 25.0  # hard ceiling for the whole fetch_sample call
# Granular HTTP timeouts so a slow host fails fast instead of stalling.
_HTTP_TIMEOUT = httpx.Timeout(connect=8.0, read=8.0, write=8.0, pool=8.0)


async def _assert_public_host(url: str) -> None:
    """Refuse URLs that are not plain http(s) to a publicly routable host.

    Resolves DNS on the event loop (non-blocking) with its own timeout, so a
    stalling resolver can't hang the tool. Blocks SSRF targets: private ranges,
    loopback, link-local (cloud metadata). A DNS-rebinding TOCTOU window remains
    since httpx re-resolves at connect time — acceptable for this dev tool.
    """
    parsed = httpx.URL(url)
    if parsed.scheme not in ("http", "https"):
        raise ToolError(f"Only http(s) URLs can be sampled, got: {url}")
    host = parsed.host
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM),
            timeout=_DNS_TIMEOUT,
        )
    except asyncio.TimeoutError as exc:
        raise ToolError(
            f"DNS lookup for {host!r} timed out after {_DNS_TIMEOUT:.0f}s "
            f"(resolver may be down or the host unreachable from the server)."
        ) from exc
    except socket.gaierror as exc:
        raise ToolError(f"Cannot resolve host {host!r}: {exc}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise ToolError(
                f"Refusing to fetch {host!r}: resolves to non-public address {ip}"
            )


async def _do_fetch_sample(url: str, max_bytes: int) -> dict:
    """Stream a bounded sample from `url`, following redirects with re-validation."""
    # Deliberately a fresh client: never send the Dataverse key to external hosts.
    async with httpx.AsyncClient(
        timeout=_HTTP_TIMEOUT, follow_redirects=False
    ) as client:
        current = url
        for _ in range(_REDIRECT_LIMIT + 1):
            await _assert_public_host(current)
            async with client.stream("GET", current) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location")
                    if not location:
                        raise ToolError(f"Redirect without Location from {current}")
                    current = str(httpx.URL(current).join(location))
                    continue  # re-validate the new host before following

                response.raise_for_status()

                buf = bytearray()
                async for chunk in response.aiter_bytes():
                    buf.extend(chunk)
                    if len(buf) > max_bytes:
                        break

                content_length = response.headers.get("content-length")
                return {
                    "url": url,
                    "final_url": current,
                    "content_type": response.headers.get("content-type"),
                    "content_length": int(content_length) if content_length else None,
                    "sample_bytes": min(len(buf), max_bytes),
                    "truncated": len(buf) > max_bytes,
                    "sample": bytes(buf[:max_bytes]).decode("utf-8", errors="replace"),
                }
        raise ToolError(f"More than {_REDIRECT_LIMIT} redirects starting from {url}")


@mcp.tool
async def fetch_sample(doi: str, url: str, max_bytes: int = 65_536) -> dict:
    """Fetch a bounded sample of a data file documented in a dataset's codebook.

    Downloads at most `max_bytes` (cap 256 KiB) from `url` and returns the
    sample text plus size/truncation info — enough to spot-check structure
    before committing to a full client-side download. `url` must appear in the
    dataset's codebook.md; arbitrary URLs are refused. Binary files come back
    lossily decoded — use this for text formats (CSV, JSON, GeoJSON, .js).

    Always returns within ~25s: a slow or unreachable host yields a ToolError
    describing the failure, never a silent hang.
    """
    max_bytes = min(max_bytes, MAX_SAMPLE_BYTES)
    codebook = await _fetch_codebook(doi)
    if url not in codebook:
        known = sorted(set(re.findall(r"https?://[^\s)\"'`<>\]]+", codebook)))
        shown = known[:20]
        more = (
            f" (+{len(known) - len(shown)} more in the codebook)"
            if len(known) > len(shown)
            else ""
        )
        raise ToolError(
            f"URL is not documented in the codebook of {_normalize_doi(doi)}; "
            f"refusing to fetch it. Documented URLs include: {shown}{more}"
        )

    try:
        return await asyncio.wait_for(
            _do_fetch_sample(url, max_bytes), timeout=_OVERALL_TIMEOUT
        )
    except asyncio.TimeoutError as exc:
        raise ToolError(
            f"Timed out after {_OVERALL_TIMEOUT:.0f}s fetching {url}. The host "
            f"is likely slow or unreachable from the MCP server. Verify the "
            f"host is up, then download the file with your own tools if your "
            f"environment can reach it."
        ) from exc
    except httpx.TimeoutException as exc:
        raise ToolError(f"HTTP timeout fetching {url}: {exc}") from exc
    except httpx.HTTPStatusError as exc:
        raise ToolError(
            f"{url} returned HTTP {exc.response.status_code}."
        ) from exc
    except httpx.HTTPError as exc:
        raise ToolError(f"Failed to fetch {url}: {exc}") from exc


@mcp.tool
async def eda(doi: str) -> str:
    """Get a self-contained EDA task briefing for a dataset.

    Returns instructions for YOU (the calling agent) to perform exploratory
    data analysis client-side, bundled with the dataset's metadata and its
    codebook. Follow the briefing: acquire the data with your own tools, parse
    it per the codebook, and produce a short report with 2-3 visualizations.

    The final report should be in html and pdf.
    """
    codebook = await _fetch_codebook(doi)
    metadata = await _fetch_dataset_meta(doi)
    return EDA_INSTRUCTIONS.format(
        title=metadata.get("title") or "untitled",
        doi=metadata.get("doi") or _normalize_doi(doi),
        metadata=json.dumps(metadata, ensure_ascii=False, indent=2),
        codebook=codebook,
    )


@mcp.resource("resource://about")
def about() -> str:
    """Basic information about this server."""
    return "ClimateVerse MCP server — scaffolding. Add your resources here."


@mcp.prompt
def summarize(topic: str) -> str:
    """Example prompt template."""
    return f"Summarize the latest information about {topic} in a concise paragraph."


def main() -> None:
    """Run the server over stdio (default transport)."""
    if not get_settings().api_key:
        # stderr only — stdout is reserved for the MCP protocol on stdio.
        print(
            "warning: CLIMATEVERSE_API_KEY is not set; API-backed tools will fail.",
            file=sys.stderr,
        )
    mcp.run()


if __name__ == "__main__":
    main()
