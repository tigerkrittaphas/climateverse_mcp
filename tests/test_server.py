"""Smoke tests for the ClimateVerse MCP server."""

import re

import pytest
from fastmcp import Client
from fastmcp.client.elicitation import ElicitResult
from fastmcp.exceptions import ToolError

from climateverse_mcp import api, server
from climateverse_mcp.instruction import AGENT_TOOLING, EDA_INSTRUCTIONS, REPORT_TEMPLATE
from climateverse_mcp.report import render_report as render_report_html
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
