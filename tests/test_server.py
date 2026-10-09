"""Smoke tests for the ClimateVerse MCP server."""

import json
import re

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.elicitation import ElicitResult
from fastmcp.exceptions import ToolError

from climateverse_mcp import aifindr, api, report_store, server
from climateverse_mcp.auth import AuthConfigError, build_auth
from climateverse_mcp.instruction import (
    AGENT_TOOLING,
    EDA_INSTRUCTIONS,
    INSTRUCTIONS,
    REPORT_TEMPLATE,
    RESEARCH_INSTRUCTIONS,
)
from climateverse_mcp.report import render_report as render_report_html
from climateverse_mcp.server import _assert_public_host, _normalize_doi, mcp
from climateverse_mcp.settings import Settings


@pytest.fixture
async def client(monkeypatch):
    # Hermetic settings: ignore the developer's real .env / env vars so the
    # no-credential path is exercised without touching the live API.
    monkeypatch.setattr(
        api,
        "get_settings",
        lambda: Settings(api_key="", api_base_url="", _env_file=None),
    )
    monkeypatch.setattr(
        aifindr,
        "get_settings",
        lambda: Settings(
            api_key="",
            api_base_url="",
            aifindr_api_key="",
            aifindr_org_id="",
            aifindr_project_id="",
            _env_file=None,
        ),
    )
    monkeypatch.setattr(
        server,
        "get_settings",
        lambda: Settings(search_provider="aifindr", _env_file=None),
    )
    async with Client(mcp) as c:
        yield c


async def test_ping(client: Client):
    result = await client.call_tool("ping")
    assert result.data == "pong"


async def test_search_requires_aifindr_configuration(client: Client):
    with pytest.raises(ToolError, match="AIFINDR_API_KEY"):
        await client.call_tool("search_datasets", {"query": "rainfall"})


def test_aifindr_sources_are_deduplicated_by_doi():
    sources = [
        {
            "title": "Heat dataset",
            "url": "https://india.climateverse.net/dataset.xhtml?persistentId=doi:10.71646/ABC123",
            "content": "First matching chunk",
            "distance": 0.1,
        },
        {
            "title": "Heat dataset",
            "sourceId": "doi:10.71646/ABC123",
            "content": "Second matching chunk",
            "distance": 0.2,
        },
        {
            "title": "Rainfall",
            "url": "https://doi.org/10.5072/FK2/RAIN01",
            "content": "Another dataset",
            "distance": 0.3,
        },
    ]

    datasets = aifindr._normalize_sources(
        sources,
        limit=10,
        dataset_base_url="https://india.climateverse.net",
    )

    assert [item["doi"] for item in datasets] == [
        "doi:10.71646/ABC123",
        "doi:10.5072/FK2/RAIN01",
    ]
    assert datasets[0]["match_rank"] == 1
    assert datasets[1]["match_rank"] == 3
    assert datasets[0]["url"].endswith("persistentId=doi:10.71646/ABC123")
    assert datasets[1]["url"] == "https://doi.org/10.5072/FK2/RAIN01"


def test_aifindr_parses_public_metadata_from_dataverse_export():
    content = {
        "status": "OK",
        "data": [
            {
                "datasetUrl": "https://india.climateverse.net/dataset.xhtml?persistentId=doi:10.71646/HEAT01",
                "datasetPersistentId": "doi:10.71646/HEAT01",
                "metadataBlocks": {
                    "citation": {
                        "fields": [
                            {"typeName": "title", "value": "Extreme Heat"},
                            {
                                "typeName": "dsDescription",
                                "value": [
                                    {
                                        "typeName": "dsDescriptionValue",
                                        "value": "District-level heat observations.",
                                    }
                                ],
                            },
                        ]
                    }
                },
            }
        ],
    }
    datasets = aifindr._normalize_sources(
        [
            {
                "title": "",
                "url": "/private/ingestion/path/dataset_1.json",
                "content": json.dumps(content),
                "distance": 0,
            }
        ],
        limit=5,
    )

    assert datasets == [
        {
            "doi": "doi:10.71646/HEAT01",
            "title": "Extreme Heat",
            "description": "District-level heat observations.",
            "url": "https://india.climateverse.net/dataset.xhtml?persistentId=doi:10.71646/HEAT01",
            "match_rank": 1,
            "distance": 0,
        }
    ]
    assert "/private/ingestion/path" not in str(datasets)


@pytest.mark.parametrize(
    "url",
    [
        "/private/ingestion/path/dataset.json",
        "http://localhost/dataset.json",
        "http://127.0.0.1/dataset.json",
        "https://catalog.internal/dataset.json",
        "ftp://india.climateverse.net/dataset.json",
        "https://user:password@example.com/dataset.json",
    ],
)
def test_aifindr_does_not_expose_non_public_source_urls(url):
    datasets = aifindr._normalize_sources(
        [
            {
                "title": "Heat doi:10.71646/HEAT01",
                "url": url,
                "content": "Heat observations",
            }
        ],
        limit=1,
    )

    assert datasets[0]["url"] is None


async def test_search_uses_one_hybrid_aifindr_request(monkeypatch):
    settings = Settings(
        aifindr_base_url="https://api.example.com",
        aifindr_api_key="key_test",
        aifindr_org_id="org_test",
        aifindr_project_id="prj_test",
        aifindr_search_alpha=0.7,
        _env_file=None,
    )
    monkeypatch.setattr(aifindr, "get_settings", lambda: settings)
    seen = []

    async def handler(request):
        seen.append(request)
        return httpx.Response(
            200,
            request=request,
            json={
                "sources": [
                    {
                        "title": "Heat",
                        "url": "https://india.climateverse.net/dataset.xhtml?persistentId=doi:10.71646/HEAT01",
                        "content": "District heat-wave observations",
                        "distance": 0.12,
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def mock_client(*args, **kwargs):
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr(aifindr.httpx, "AsyncClient", mock_client)
    result = await aifindr.search_aifindr("extreme heat", 4)

    assert len(seen) == 1
    assert seen[0].headers["authorization"] == "Bearer key_test"
    assert seen[0].headers["x-organization-id"] == "org_test"
    assert b'"alpha":0.7' in seen[0].content
    assert b'"limit":12' in seen[0].content
    assert result["datasets"][0]["doi"] == "doi:10.71646/HEAT01"
    assert result["retrieval"]["provider"] == "aifindr"


@pytest.mark.parametrize(
    "payload",
    [
        b"not json",
        json.dumps({"sources": {"unexpected": "shape"}}).encode(),
        json.dumps([{"sources": []}]).encode(),
    ],
)
async def test_aifindr_rejects_malformed_success_responses(monkeypatch, payload):
    settings = Settings(
        aifindr_base_url="https://api.example.com",
        aifindr_api_key="key_test",
        aifindr_org_id="org_test",
        aifindr_project_id="prj_test",
        _env_file=None,
    )
    monkeypatch.setattr(aifindr, "get_settings", lambda: settings)

    async def handler(request):
        return httpx.Response(200, request=request, content=payload)

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def mock_client(*args, **kwargs):
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr(aifindr.httpx, "AsyncClient", mock_client)
    with pytest.raises(ToolError, match="invalid"):
        await aifindr.search_aifindr("heat", 5)


async def test_search_provider_defaults_to_aifindr(monkeypatch):
    settings = Settings(_env_file=None)
    monkeypatch.setattr(server, "get_settings", lambda: settings)

    async def fake_aifindr(query, limit):
        return {"provider": "aifindr", "query": query, "limit": limit}

    monkeypatch.setattr(server, "search_aifindr", fake_aifindr)
    result = await server._search(" heat ", 3)

    assert result == {"provider": "aifindr", "query": "heat", "limit": 3}


def test_settings_ignore_removed_dotenv_keys(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("AIFINDR_KNOWLEDGE_VERSION=v3\n")

    settings = Settings(_env_file=env_file)

    assert settings.search_provider == "aifindr"


async def test_search_provider_can_select_dataverse(monkeypatch):
    settings = Settings(search_provider="dataverse", _env_file=None)
    monkeypatch.setattr(server, "get_settings", lambda: settings)

    async def fake_dataverse(query, limit):
        return {"provider": "dataverse", "query": query, "limit": limit}

    monkeypatch.setattr(server, "_dataverse_search", fake_dataverse)
    result = await server._search(" rainfall ", 4)

    assert result == {
        "provider": "dataverse",
        "query": "rainfall",
        "limit": 4,
    }


def test_agent_guidance_bounds_discovery_searches():
    guidance = " ".join((INSTRUCTIONS + RESEARCH_INSTRUCTIONS).split())
    assert "at most one targeted" in guidance
    assert "Do not issue one search per synonym" in guidance
    assert "do not use list_datasets as a fallback catalog scan" in guidance
    assert "If two codebook calls fail" in guidance
    assert "Never call or suggest fetch_sample" in guidance
    assert "re-search with several phrasings" not in guidance


async def test_clarify_research_question_elicits_scope():
    seen_messages = []

    async def answer_scope(message, response_type, _params, _ctx):
        seen_messages.append(message)
        return ElicitResult(
            action="accept",
            content=response_type(
                research_goal="Compare heat exposure and power outages",
                geography="Districts in Odisha, India",
                time_period="Pre-monsoon months, 2015-2024",
                indicator_definition=(
                    "Compare temperature-threshold heat days with reported "
                    "disastrous heat-wave days"
                ),
                comparison_or_baseline="Districts and years",
            ),
        )

    async with Client(mcp, elicitation_handler=answer_scope) as eliciting_client:
        result = await eliciting_client.call_tool(
            "clarify_research_question",
            {"question": "Explore heat and outages in Odisha"},
        )

    assert seen_messages and "Current request" in seen_messages[0]
    assert result.data["status"] == "clarified"
    assert result.data["context"]["geography"] == "Districts in Odisha, India"
    assert "Pre-monsoon months, 2015-2024" in result.data["research_brief"]
    assert "compare temperature-threshold heat days" in result.data[
        "research_brief"
    ].lower()
    # Defaults reflect the internal, exploratory report described by the project.
    assert "new to the topic" in result.data["context"]["audience"]
    assert "not for publication" in result.data["context"]["intended_use"]
    assert "research()" in result.data["next_step"]


async def test_clarify_research_question_handles_decline():
    async def decline_scope(_message, _response_type, _params, _ctx):
        return ElicitResult(action="decline")

    async with Client(mcp, elicitation_handler=decline_scope) as eliciting_client:
        result = await eliciting_client.call_tool(
            "clarify_research_question",
            {"question": "What is happening with rainfall?"},
        )

    assert result.data["status"] == "decline"
    assert "Do not guess" in result.data["message"]


async def test_clarify_research_question_rejects_empty_question(client: Client):
    with pytest.raises(ToolError, match="question or topic is required"):
        await client.call_tool("clarify_research_question", {"question": "  "})


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


# --- report rendering ------------------------------------------------------


def test_render_report_is_branded_and_self_contained():
    """The identity must come from the server, not from the calling agent."""
    document, warnings = render_report_html(
        title="Heat Wave Days",
        body_html='<p class="cv-lead">Body.</p>',
        doi="doi:10.71646/K9ZHZ7",
    )
    assert not warnings
    # Logo and stylesheet are injected, so no agent has to reproduce them.
    assert "data:image/png;base64," in document
    assert "--cv-brand-orange" in document and ".cv-callout" in document
    # Self-contained: nothing to fetch at render or print time.
    assert "<link" not in document and "http://" not in document
    # Light is the default and cannot be flipped by the viewer's OS.
    assert 'data-theme="light"' in document
    assert "@media (prefers-color-scheme" not in document
    assert "doi:10.71646/K9ZHZ7" in document


def test_render_report_escapes_text_fields():
    """Title/lead/meta are plain text; only body_html is trusted markup."""
    document, _ = render_report_html(
        title='Heat <script>alert(1)</script>',
        body_html="<p>ok</p>",
        lead="A & B",
    )
    assert "<script>alert(1)</script>" not in document
    assert "&lt;script&gt;" in document
    assert "A &amp; B" in document


def test_render_report_warns_on_unknown_tone_and_external_refs():
    _, warnings = render_report_html(
        title="T",
        body_html=(
            '<div class="cv-callout" data-tone="critical">x</div>'
            '<img src="https://example.com/fig.png" />'
        ),
    )
    assert any("critical" in w for w in warnings)
    assert any("self-contained" in w for w in warnings)


async def test_render_report_tool_writes_file(client: Client, tmp_path):
    out = tmp_path / "nested" / "report.html"
    result = await client.call_tool(
        "render_report",
        {
            "out_path": str(out),
            "title": "Heat Wave Days",
            "body_html": '<p class="cv-lead">Body.</p>',
        },
    )
    assert out.exists()
    assert result.data["path"] == str(out)
    assert result.data["bytes"] == len(out.read_text(encoding="utf-8").encode())
    # The document itself is never returned — it would swamp the caller's context.
    assert "<!doctype html>" not in str(result.data)


async def test_render_report_tool_rejects_non_html_path(client: Client, tmp_path):
    with pytest.raises(ToolError, match="must end in .html"):
        await client.call_tool(
            "render_report",
            {"out_path": str(tmp_path / "report.pdf"), "title": "T", "body_html": "<p>x</p>"},
        )


# --- research briefing -----------------------------------------------------


@pytest.fixture
def stub_search(monkeypatch):
    """Stub the catalog search so research() runs without network access."""
    hits = {
        "total_count": 2,
        "returned": 2,
        "datasets": [
            {
                "doi": "doi:10.71646/K9ZHZ7",
                "title": "IMD Disastrous Heat Wave Days",
                "description": "District counts of fatal heat-wave days. " * 20,
            },
            {"doi": "doi:10.71646/MUTGWY", "title": "IMD Gridded Tmax", "description": ""},
        ],
    }

    async def _fake_search(query, limit):
        return hits

    monkeypatch.setattr(server, "_search", _fake_search)
    return hits


async def test_research_briefing_carries_question_candidates_and_join_audit(
    client: Client, stub_search
):
    briefing = (
        await client.call_tool("research", {"question": "Does heat track outages?"})
    ).data
    assert "Does heat track outages?" in briefing
    # Candidates are seeded so the agent starts from real DOIs, not guesses.
    assert "doi:10.71646/K9ZHZ7" in briefing and "IMD Gridded Tmax" in briefing
    # The join audit is the point of this tool over eda().
    assert "Row counts before and after" in briefing
    assert "Telangana" in briefing  # boundary instability is called out concretely
    # Reporting still goes through the branded renderer.
    assert "render_report" in briefing and "cv-stat" in briefing
    # Long descriptions are trimmed rather than dumped whole.
    assert "..." in briefing
    # Without `dois`, no codebook is bundled — that is the caller's choice.
    assert "Bundled sources" not in briefing


async def test_research_rejects_empty_question(client: Client, stub_search):
    with pytest.raises(ToolError, match="question is required"):
        await client.call_tool("research", {"question": "   "})


async def test_research_bundles_named_dois_and_caps_them(
    client: Client, stub_search, monkeypatch
):
    """Codebooks are huge, so bundling is bounded and the overflow is named."""

    async def _fake_meta(doi):
        return {"doi": doi, "title": f"Dataset {doi[-2:]}"}

    async def _fake_codebook(doi):
        return f"CODEBOOK-FOR-{doi[-2:]}"

    monkeypatch.setattr(server, "_fetch_dataset_meta", _fake_meta)
    monkeypatch.setattr(server, "_fetch_codebook", _fake_codebook)

    briefing = (
        await client.call_tool(
            "research",
            {"question": "q", "dois": ["doi:a1", "doi:b2", "doi:c3", "doi:d4"]},
        )
    ).data
    assert "Bundled sources" in briefing
    for tail in ("a1", "b2", "c3"):
        assert f"CODEBOOK-FOR-{tail}" in briefing
    # The fourth is not bundled, but the agent is told it exists and how to get it.
    assert "CODEBOOK-FOR-d4" not in briefing
    assert "doi:d4" in briefing and "get_codebook" in briefing


async def test_research_survives_one_unavailable_doi(
    client: Client, stub_search, monkeypatch
):
    """One bad DOI must not sink a multi-source briefing."""

    async def _fake_meta(doi):
        if "bad" in doi:
            raise ToolError(f"Dataset not found: {doi}")
        return {"doi": doi, "title": "Good one"}

    async def _fake_codebook(doi):
        return "GOOD-CODEBOOK"

    monkeypatch.setattr(server, "_fetch_dataset_meta", _fake_meta)
    monkeypatch.setattr(server, "_fetch_codebook", _fake_codebook)

    briefing = (
        await client.call_tool("research", {"question": "q", "dois": ["doi:bad", "doi:ok"]})
    ).data
    assert "Could not be bundled" in briefing and "doi:bad" in briefing
    assert "GOOD-CODEBOOK" in briefing


def test_eda_briefing_embeds_the_template_contract():
    """The briefing must carry the class vocabulary but not the stylesheet."""
    briefing = EDA_INSTRUCTIONS.format(
        title="T",
        doi="D",
        metadata="{}",
        codebook="CB",
        tooling=AGENT_TOOLING,
        template=REPORT_TEMPLATE,
    )
    assert "cv-stat" in briefing and "cv-callout" in briefing
    assert "render_report" in briefing
    # The briefing must push the agent toward its own code + web tools.
    assert "code-execution" in briefing and "web search" in briefing.lower()
    # Keep the CSS and the base64 logo out of the model's context. The briefing
    # does mention `data:image/png;base64,…` as guidance, so the thing to assert
    # is that no actual payload rides along — no long base64 run, and a total
    # size far below the ~56 KB of an assembled report.
    assert "--cv-brand-orange" not in briefing
    assert not re.search(r"[A-Za-z0-9+/]{200,}", briefing)
    assert len(briefing) < 12_000


# --- hosted deployment -------------------------------------------------------


async def test_render_report_tool_requires_out_path_locally(client: Client):
    with pytest.raises(ToolError, match="out_path is required"):
        await client.call_tool("render_report", {"title": "T", "body_html": "<p>x</p>"})


async def test_render_report_tool_stores_report_when_hosted(monkeypatch):
    monkeypatch.setattr(
        server,
        "get_settings",
        lambda: Settings(
            reports_bucket="reports",
            public_base_url="https://mcp.example.org/",
            _env_file=None,
        ),
    )
    stored = {}

    def fake_save(bucket, document):
        stored["bucket"], stored["document"] = bucket, document
        return "a" * 32

    monkeypatch.setattr(server, "save_report", fake_save)
    async with Client(mcp) as c:
        result = await c.call_tool(
            "render_report",
            {"out_path": "/ignored.html", "title": "T", "body_html": "<p>x</p>"},
        )
    assert result.data["url"] == f"https://mcp.example.org/reports/{'a' * 32}.html"
    assert stored["bucket"] == "reports"
    assert result.data["bytes"] == len(stored["document"].encode())


def _http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=mcp.http_app()), base_url="http://test"
    )


async def test_health_route():
    async with _http_client() as http:
        response = await http.get("/health")
    assert response.status_code == 200 and response.text == "ok"


async def test_report_route_serves_sandboxed_html(monkeypatch):
    monkeypatch.setattr(
        server, "get_settings", lambda: Settings(reports_bucket="b", _env_file=None)
    )
    monkeypatch.setattr(
        server,
        "load_report",
        lambda bucket, rid: b"<html>r</html>" if rid == "a" * 32 else None,
    )
    async with _http_client() as http:
        found = await http.get(f"/reports/{'a' * 32}.html")
        missing = await http.get(f"/reports/{'b' * 32}.html")
    assert found.status_code == 200 and found.text == "<html>r</html>"
    assert found.headers["content-security-policy"].startswith("sandbox")
    assert missing.status_code == 404


def test_load_report_rejects_malformed_ids_without_calling_s3(monkeypatch):
    def boom():
        raise AssertionError("S3 must not be called")

    monkeypatch.setattr(report_store, "_s3", boom)
    assert report_store.load_report("b", "../secret") is None


def test_http_auth_refuses_partial_configuration():
    settings = Settings(cognito_user_pool_id="pool", _env_file=None)
    with pytest.raises(AuthConfigError, match="CLIMATEVERSE_COGNITO_CLIENT_ID"):
        build_auth(settings)


def test_http_auth_requires_explicit_opt_out():
    with pytest.raises(AuthConfigError, match="ALLOW_UNAUTHENTICATED"):
        build_auth(Settings(_env_file=None))
    assert build_auth(Settings(allow_unauthenticated=True, _env_file=None)) is None


async def test_api_client_is_anonymous_without_key(monkeypatch):
    monkeypatch.setattr(
        api,
        "get_settings",
        lambda: Settings(api_key="", api_base_url="https://dv.example.org", _env_file=None),
    )
    async with api.api_client() as anonymous:
        assert "X-Dataverse-key" not in anonymous.headers

    monkeypatch.setattr(
        api,
        "get_settings",
        lambda: Settings(api_key="k", api_base_url="https://dv.example.org", _env_file=None),
    )
    async with api.api_client() as authenticated:
        assert authenticated.headers["X-Dataverse-key"] == "k"


@pytest.fixture
def fake_dataverse(monkeypatch):
    """Route api_client() to an in-memory Dataverse that records the key sent."""
    seen: list[str | None] = []
    status = {"code": 200}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("x-dataverse-key"))
        if status["code"] != 200:
            return httpx.Response(status["code"], json={"status": "ERROR"})
        return httpx.Response(200, json={"data": {"total_count": 0, "items": []}})

    real_client = httpx.AsyncClient

    class Routed(real_client):
        def __init__(self, *args, **kwargs):
            if str(kwargs.get("base_url", "")).startswith("https://dv.test"):
                kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Routed)
    settings = Settings(
        api_key="server-key",
        api_base_url="https://dv.test",
        search_provider="dataverse",
        _env_file=None,
    )
    monkeypatch.setattr(api, "get_settings", lambda: settings)
    monkeypatch.setattr(server, "get_settings", lambda: settings)
    return seen, status


async def test_dataverse_key_resolution_over_http(fake_dataverse):
    # One server for all cases: the module-level `mcp` cannot be restarted on
    # a second test's event loop.
    from fastmcp.client.transports import StreamableHttpTransport
    from fastmcp.utilities.tests import run_server_async

    seen, status = fake_dataverse

    async def list_datasets(headers=None):
        async with Client(StreamableHttpTransport(url, headers=headers)) as c:
            return await c.call_tool("list_datasets", {"limit": 1})

    async with run_server_async(mcp) as url:
        # The key the MCP client sends wins over the server's own.
        await list_datasets({"X-Dataverse-Key": "user-key"})
        assert seen[-1] == "user-key"

        # Without one, the server's CLIMATEVERSE_API_KEY applies.
        await list_datasets()
        assert seen[-1] == "server-key"

        # A key Dataverse rejects is reported as such, not as a bare 401.
        status["code"] = 401
        with pytest.raises(ToolError, match="Dataverse rejected the API key"):
            await list_datasets({"X-Dataverse-Key": "expired"})


def _response(status: int, content_type: str) -> httpx.Response:
    request = httpx.Request("GET", "https://dv.test/api/x")
    return httpx.Response(status, headers={"content-type": content_type}, request=request)


def test_dataverse_refusals_explain_cause(monkeypatch):
    monkeypatch.setattr(
        api, "get_settings", lambda: Settings(api_key="", api_base_url="https://dv.test", _env_file=None)
    )
    # Load-balancer WAF block (HTML), not a Dataverse permission decision.
    with pytest.raises(ToolError, match="rate limit"):
        api.raise_for_dataverse_status(_response(403, "text/html"), "Dataset X")
    # Dataverse refusal while anonymous points at the key header.
    with pytest.raises(ToolError, match="X-Dataverse-Key"):
        api.raise_for_dataverse_status(_response(403, "application/json"), "Dataset X")
    with pytest.raises(ToolError, match="not found or not visible"):
        api.raise_for_dataverse_status(_response(404, "application/json"), "Dataset X")


async def test_research_bundle_survives_http_errors(monkeypatch):
    async def meta(doi):
        return {"title": "T", "doi": doi}

    async def codebook(doi):
        raise httpx.HTTPStatusError(
            "boom", request=httpx.Request("GET", "https://dv.test"), response=_response(500, "text/plain")
        )

    monkeypatch.setattr(server, "_fetch_dataset_meta", meta)
    monkeypatch.setattr(server, "_fetch_codebook", codebook)
    bundle = await server._bundle_source("doi:10.1/X")
    assert "Could not be bundled" in bundle
