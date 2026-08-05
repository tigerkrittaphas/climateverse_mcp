"""ClimateVerse MCP server."""

import asyncio
import ipaddress
import json
import re
import socket
import sys
from pathlib import Path

import httpx
from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field

from .api import api_client
from .instruction import (
    AGENT_TOOLING,
    EDA_INSTRUCTIONS,
    INSTRUCTIONS,
    REPORT_TEMPLATE,
    RESEARCH_INSTRUCTIONS,
    RESEARCH_REPORT_STRUCTURE,
)
from .report import render_report as render_report_html
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


class ResearchClarification(BaseModel):
    """User-supplied scope for an exploratory climate-data question."""

    research_goal: str = Field(
        description=(
            "The question to answer or pattern, comparison, or decision to explore"
        )
    )
    geography: str = Field(
        description="Place and geographic level, such as country, state, or district"
    )
    time_period: str = Field(
        description=(
            "Years and relevant season or months; enter 'not sure' if exploratory"
        )
    )
    indicator_definition: str = Field(
        description=(
            "How the main outcome or exposure should be measured, including any "
            "threshold; enter 'compare definitions' if undecided"
        )
    )
    comparison_or_baseline: str = Field(
        default="No specific comparison or baseline",
        description="Groups, periods, places, or baseline to compare",
    )
    audience: str = Field(
        default="People and researchers new to the topic",
        description="Who will read the analysis and their level of subject knowledge",
    )
    intended_use: str = Field(
        default=(
            "Internal exploratory working note; encourage further analysis; "
            "not for publication"
        ),
        description="How the report will be used and whether it may be published",
    )


def _format_research_clarification(
    question: str, clarification: ResearchClarification
) -> dict:
    """Turn elicited fields into a portable brief for subsequent tool calls."""
    context = clarification.model_dump()
    lines = [
        f"Original request: {question.strip()}",
        f"Research goal: {clarification.research_goal.strip()}",
        f"Geography: {clarification.geography.strip()}",
        f"Time period / season: {clarification.time_period.strip()}",
        f"Indicator definition: {clarification.indicator_definition.strip()}",
        f"Comparison / baseline: {clarification.comparison_or_baseline.strip()}",
        f"Audience: {clarification.audience.strip()}",
        f"Intended use: {clarification.intended_use.strip()}",
    ]
    return {
        "status": "clarified",
        "original_question": question.strip(),
        "context": context,
        "research_brief": "\n".join(lines),
        "next_step": (
            "Use research_brief as the question passed to research(), and preserve "
            "these scope choices in the analysis and report."
        ),
    }


@mcp.tool
async def clarify_research_question(question: str, ctx: Context) -> dict:
    """Ask the user for missing scope before climate-data research begins.

    Call this BEFORE research() when a request leaves any consequential choice
    unclear: the research goal, geography or geographic level, years/season,
    indicator definition or threshold, comparison/baseline, audience, or
    intended use. Do not silently choose these on the user's behalf.

    The client must support MCP elicitation. The user may enter "not sure" or
    "compare definitions" when exploration, rather than a fixed choice, is the
    goal. The returned research_brief can be passed directly to research().
    """
    if not question.strip():
        raise ToolError("A research question or topic is required.")

    result = await ctx.elicit(
        (
            "Before searching the ClimateVerse catalog, please clarify the scope "
            "of this exploratory request. It is fine to answer 'not sure' where "
            "you want the analysis to compare reasonable options.\n\n"
            f"Current request: {question.strip()}"
        ),
        response_type=ResearchClarification,
    )
    if result.action != "accept":
        return {
            "status": result.action,
            "original_question": question.strip(),
            "message": (
                "Clarification was not provided. Do not guess consequential scope; "
                "ask the user in the conversation or limit the work to catalog "
                "discovery without analysis."
            ),
        }

    return _format_research_clarification(question, result.data)


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
    data analysis client-side, bundled with the dataset's metadata, its
    codebook, and the ClimateVerse report template contract.

    Deliver the finished report by calling render_report(), which writes a
    self-contained, branded HTML file. It carries print styles, so exporting
    that file to PDF from a browser reproduces the same layout.
    """
    codebook = await _fetch_codebook(doi)
    metadata = await _fetch_dataset_meta(doi)
    return EDA_INSTRUCTIONS.format(
        title=metadata.get("title") or "untitled",
        doi=metadata.get("doi") or _normalize_doi(doi),
        metadata=json.dumps(metadata, ensure_ascii=False, indent=2),
        codebook=codebook,
        tooling=AGENT_TOOLING,
        template=REPORT_TEMPLATE,
    )


#: Codebooks are comprehensive and can run to tens of thousands of tokens each.
#: Bundling several blindly would swamp the caller's context before any analysis
#: starts, so `research` bundles only DOIs the caller explicitly named, capped.
MAX_BUNDLED_CODEBOOKS = 3
#: How many search hits to seed the briefing with.
RESEARCH_CANDIDATES = 8


def _format_candidates(datasets: list[dict]) -> str:
    """Render search hits as a compact list — titles and DOIs, not codebooks."""
    if not datasets:
        return (
            "_No datasets matched this phrasing._ That is a result about the "
            "query, not about the catalog: re-run search_datasets with "
            "synonyms, indicator names, or the publishing agency's name before "
            "concluding the data does not exist."
        )
    lines = []
    for item in datasets:
        title = item.get("title") or "untitled"
        description = (item.get("description") or "").strip().replace("\n", " ")
        if len(description) > 220:
            description = description[:217] + "..."
        lines.append(f"- **{title}** — `{item.get('doi')}`")
        if description:
            lines.append(f"  {description}")
    return "\n".join(lines)


async def _bundle_source(doi: str) -> str:
    """Fetch one dataset's metadata + codebook for inclusion in a briefing."""
    try:
        metadata = await _fetch_dataset_meta(doi)
        codebook = await _fetch_codebook(doi)
    except ToolError as exc:
        # One bad DOI must not sink the whole briefing — report it in place so
        # the agent can see which source is unavailable and carry on.
        return f"### {doi}\n\n_Could not be bundled: {exc}_\n"
    return (
        f"### {metadata.get('title') or 'untitled'} ({metadata.get('doi') or doi})\n\n"
        f"```json\n{json.dumps(metadata, ensure_ascii=False, indent=2)}\n```\n\n"
        f"#### Codebook\n\n{codebook}\n"
    )


@mcp.tool
async def research(question: str, dois: list[str] | None = None) -> str:
    """Get a task briefing for answering a RESEARCH QUESTION from the catalog.

    Call this when the user asks a question of the data rather than about one
    dataset — anything that may need several datasets, a join, or filtering.
    Use eda(doi) instead when the task is profiling a single known dataset.

    Returns instructions for YOU to execute: how to turn the question into data
    requirements, find and verify candidate datasets, decide whether they can
    legitimately be combined, audit every join and filter, and deliver the
    answer through render_report(). Candidate datasets matching the question are
    included so you can start from real DOIs rather than guesses.

    Pass `dois` once you know which datasets you need and their full metadata
    and codebooks will be bundled in (up to 3 — codebooks are large). Leave it
    empty on the first call: you will get the briefing plus candidates, then
    pull codebooks selectively with get_codebook(doi).
    """
    if not question.strip():
        raise ToolError("A research question is required.")

    found = await _search(question, RESEARCH_CANDIDATES)

    sources = ""
    if dois:
        selected = dois[:MAX_BUNDLED_CODEBOOKS]
        bundles = await asyncio.gather(*(_bundle_source(d) for d in selected))
        omitted = ""
        if len(dois) > len(selected):
            skipped = ", ".join(dois[MAX_BUNDLED_CODEBOOKS:])
            omitted = (
                f"\n_Not bundled (limit {MAX_BUNDLED_CODEBOOKS}): {skipped}. "
                f"Pull these with get_codebook(doi) as you need them._\n"
            )
        sources = "\n## Bundled sources\n\n" + "\n".join(bundles) + omitted

    return RESEARCH_INSTRUCTIONS.format(
        question=question.strip(),
        tooling=AGENT_TOOLING,
        structure=RESEARCH_REPORT_STRUCTURE,
        template=REPORT_TEMPLATE,
        candidates=_format_candidates(found.get("datasets") or []),
        sources=sources,
    )


@mcp.tool
def render_report(
    out_path: str,
    title: str,
    body_html: str,
    lead: str = "",
    doi: str = "",
    meta: list[str] | None = None,
) -> dict:
    """Wrap your report content in the branded ClimateVerse shell and write it.

    THE way to deliver a report from this server — do not hand-roll your own
    HTML page, and do not write your own CSS. This supplies the logo, masthead,
    brand rule, footer, colour system, chart palette and print styles, so every
    report from this catalog looks like it came from the same publisher.

    Write ONLY body content in `body_html`, using the `cv-*` classes listed in
    the eda() briefing. No <style>, no <script>, no external URLs — embed any
    figures as data: URIs so the file stays self-contained and prints correctly.

    `out_path` is where the .html file is written. Returns the path and byte
    count plus any warnings; the document itself is not returned, since a
    finished report is far too large to be useful in your context.
    """
    document, warnings = render_report_html(
        title=title, body_html=body_html, lead=lead, doi=doi, meta=meta
    )

    destination = Path(out_path).expanduser()
    if destination.suffix.lower() not in (".html", ".htm"):
        raise ToolError(f"out_path must end in .html, got: {out_path}")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(document, encoding="utf-8")
    except OSError as exc:
        raise ToolError(f"Could not write {destination}: {exc}") from exc

    return {
        "path": str(destination),
        "bytes": len(document.encode("utf-8")),
        "warnings": warnings,
    }


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
