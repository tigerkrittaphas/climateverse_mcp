"""AIFindr hybrid-search client and dataset-level result normalization."""

import ipaddress
import json
import re
from urllib.parse import quote, unquote, urlsplit

import httpx
from fastmcp.exceptions import ToolError

from .settings import get_settings


_DOI_RE = re.compile(r"(?:doi:)?(10\.\d{4,9}/[A-Z0-9._;()/:+-]+)", re.IGNORECASE)
_MAX_MATCH_CHARS = 800


def _extract_doi(*values: object) -> str | None:
    """Extract and normalize the first DOI found in source metadata or content."""
    for value in values:
        if value is None:
            continue
        match = _DOI_RE.search(unquote(str(value)))
        if match:
            normalized = match.group(1).rstrip(".,;:)]}")
            return f"doi:{normalized}"
    return None


def _compact_match(content: object) -> str | None:
    if content is None:
        return None
    text = " ".join(str(content).split())
    if not text:
        return None
    return text if len(text) <= _MAX_MATCH_CHARS else text[: _MAX_MATCH_CHARS - 3] + "..."


def _unwrap_dataverse_value(value):
    if isinstance(value, list):
        return [_unwrap_dataverse_value(item) for item in value]
    if isinstance(value, dict):
        if "typeName" in value and "value" in value:
            return _unwrap_dataverse_value(value["value"])
        return {key: _unwrap_dataverse_value(item) for key, item in value.items()}
    return value


def _metadata_field(record: dict, type_name: str):
    for block in (record.get("metadataBlocks") or {}).values():
        for field in block.get("fields") or []:
            if field.get("typeName") == type_name:
                return _unwrap_dataverse_value(field.get("value"))
    return None


def _parse_dataverse_source(content: object) -> dict:
    """Extract public dataset metadata from an indexed Dataverse API export."""
    if not isinstance(content, str):
        return {}
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return {}

    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, list):
        record = next((item for item in data if isinstance(item, dict)), None)
    elif isinstance(data, dict):
        record = data
    else:
        record = payload if isinstance(payload, dict) else None
    if not record:
        return {}

    descriptions = _metadata_field(record, "dsDescription") or []
    description = None
    if isinstance(descriptions, list) and descriptions:
        first = descriptions[0]
        if isinstance(first, dict):
            description = first.get("dsDescriptionValue")
        elif isinstance(first, str):
            description = first

    return {
        "doi": record.get("datasetPersistentId"),
        "title": _metadata_field(record, "title"),
        "description": description,
        "url": record.get("datasetUrl"),
    }


def _public_http_url(value: object) -> str | None:
    """Return a public-looking HTTP URL, excluding paths and credentialed URLs."""
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value.strip())
    hostname = parsed.hostname
    if (
        parsed.scheme not in {"http", "https"}
        or not hostname
        or parsed.username
        or parsed.password
    ):
        return None
    if hostname == "localhost" or hostname.endswith((".local", ".internal")):
        return None
    try:
        if ipaddress.ip_address(hostname).is_private:
            return None
    except ValueError:
        pass
    return value.strip()


def _canonical_dataset_url(doi: str, dataset_base_url: str) -> str | None:
    base_url = _public_http_url(dataset_base_url)
    if not base_url:
        return None
    encoded_doi = quote(doi, safe=":/")
    return f"{base_url.rstrip('/')}/dataset.xhtml?persistentId={encoded_doi}"


def _normalize_sources(
    sources: list[dict], limit: int, dataset_base_url: str = ""
) -> list[dict]:
    """Collapse ranked knowledge chunks into unique dataset candidates."""
    datasets: list[dict] = []
    seen: set[str] = set()

    for rank, source in enumerate(sources, start=1):
        parsed = _parse_dataverse_source(source.get("content"))
        doi = _extract_doi(
            parsed.get("doi"),
            parsed.get("url"),
            source.get("url"),
            source.get("sourceId"),
            source.get("title"),
            source.get("content"),
        )
        if not doi or doi in seen:
            continue
        seen.add(doi)
        dataset_url = (
            _public_http_url(parsed.get("url"))
            or _public_http_url(source.get("url"))
            or _canonical_dataset_url(doi, dataset_base_url)
        )
        datasets.append(
            {
                "doi": doi,
                "title": parsed.get("title") or source.get("title") or "untitled",
                "description": _compact_match(
                    parsed.get("description") or source.get("content")
                ),
                "url": dataset_url,
                "match_rank": rank,
                "distance": source.get("distance"),
            }
        )
        if len(datasets) == limit:
            break

    return datasets


async def search_aifindr(query: str, limit: int) -> dict:
    """Run one AIFindr hybrid search and return DOI-level candidates."""
    settings = get_settings()
    missing = [
        name
        for name, value in (
            ("AIFINDR_BASE_URL", settings.aifindr_base_url),
            ("AIFINDR_API_KEY", settings.aifindr_api_key),
            ("AIFINDR_ORG_ID", settings.aifindr_org_id),
            ("AIFINDR_PROJECT_ID", settings.aifindr_project_id),
        )
        if not value
    ]
    if missing:
        raise ToolError(f"Missing required AIFindr configuration: {', '.join(missing)}")

    # Knowledge search returns chunks rather than datasets. Overfetch once, then
    # deduplicate locally, avoiding repeated agent-visible search tool calls.
    request_limit = min(max(limit * 3, limit), 200)
    payload = {
        "query": query,
        "limit": request_limit,
        "offset": 0,
        "alpha": settings.aifindr_search_alpha,
    }
    url = (
        f"{settings.aifindr_base_url.rstrip('/')}"
        f"/api/private/projects/{settings.aifindr_project_id}/rag-search"
    )
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {settings.aifindr_api_key}",
                    "X-Organization-Id": settings.aifindr_org_id,
                    "Accept": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        hint = (
            " The key needs source:get for the configured AIFindr project."
            if status == 403
            else ""
        )
        raise ToolError(f"AIFindr search returned HTTP {status}.{hint}") from exc
    except httpx.HTTPError as exc:
        raise ToolError(f"AIFindr search failed: {exc}") from exc

    try:
        raw = response.json()
    except ValueError as exc:
        raise ToolError("AIFindr search returned invalid JSON.") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("sources"), list):
        raise ToolError("AIFindr search returned an invalid response.")

    sources = raw["sources"]
    datasets = _normalize_sources(sources, limit, settings.api_base_url)
    return {
        "total_count": None,
        "returned": len(datasets),
        "datasets": datasets,
        "retrieval": {
            "provider": "aifindr",
            "mode": "hybrid",
            "alpha": settings.aifindr_search_alpha,
            "source_chunks_considered": len(sources),
        },
    }
