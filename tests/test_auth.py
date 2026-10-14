"""Sign-in with a ClimateVerse API token, end to end against a fake Dataverse."""

import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastmcp import Client, FastMCP
from key_value.aio.stores.memory import MemoryStore
from starlette.applications import Starlette

from climateverse_mcp import api
from climateverse_mcp.auth import DataverseTokenProvider
from climateverse_mcp.settings import Settings

REDIRECT = "http://localhost:33418/callback"


@pytest.fixture
def dataverse(monkeypatch):
    """A fake Dataverse at https://dv.test whose valid tokens can be revoked."""
    valid = {"good-token": "@alice"}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/users/:me"
        user = valid.get(request.headers.get("x-dataverse-key", ""))
        if user is None:
            return httpx.Response(401, json={"status": "ERROR"})
        return httpx.Response(200, json={"data": {"identifier": user}})

    real_client = httpx.AsyncClient

    class Routed(real_client):
        def __init__(self, *args, **kwargs):
            if str(kwargs.get("base_url", "")).startswith("https://dv.test"):
                kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Routed)
    return valid


@pytest.fixture
async def oauth(dataverse):
    provider = DataverseTokenProvider(
        base_url="https://mcp.test",
        dataverse_url="https://dv.test/",
        storage=MemoryStore(),
        allowed_redirect_uris=["http://localhost/*"],
    )
    app = Starlette(routes=provider.get_routes("/mcp"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://mcp.test"
    ) as http:
        yield provider, http


async def _register(http: httpx.AsyncClient) -> str:
    response = await http.post(
        "/register",
        json={
            "client_name": "Test Agent",
            "redirect_uris": [REDIRECT],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["client_id"]


async def _start_login(http: httpx.AsyncClient, client_id: str) -> tuple[str, str]:
    """Authorize like an MCP client; return the login request id and PKCE verifier."""
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    response = await http.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "st4te",
        },
    )
    assert response.status_code == 302, response.text
    login = urlparse(response.headers["location"])
    assert login.path == "/login"
    return parse_qs(login.query)["request"][0], verifier


async def _sign_in(http: httpx.AsyncClient, token: str = "good-token") -> tuple[str, dict]:
    client_id = await _register(http)
    request_id, verifier = await _start_login(http, client_id)
    response = await http.post("/login", data={"request": request_id, "token": token})
    assert response.status_code == 303, response.text
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["state"] == ["st4te"]
    tokens = await http.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": query["code"][0],
            "redirect_uri": REDIRECT,
            "client_id": client_id,
            "code_verifier": verifier,
        },
    )
    assert tokens.status_code == 200, tokens.text
    return client_id, tokens.json()


async def test_login_page_names_the_client_and_rejects_bad_tokens(oauth):
    _, http = oauth
    request_id, _ = await _start_login(http, await _register(http))

    page = await http.get("/login", params={"request": request_id})
    assert page.status_code == 200
    assert "Test Agent" in page.text
    assert "https://dv.test/dataverseuser.xhtml?selectTab=apiTokenTab" in page.text
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]

    refused = await http.post("/login", data={"request": request_id, "token": "nope"})
    assert refused.status_code == 400
    assert "did not accept" in refused.text

    # The request survives a wrong token, then is single use.
    accepted = await http.post("/login", data={"request": request_id, "token": "good-token"})
    assert accepted.status_code == 303
    replay = await http.post("/login", data={"request": request_id, "token": "good-token"})
    assert replay.status_code == 400 and "expired" in replay.text


async def test_unknown_login_request_is_refused(oauth):
    _, http = oauth
    response = await http.get("/login", params={"request": "made-up"})
    assert response.status_code == 400


async def test_registration_refuses_unlisted_redirects(oauth):
    _, http = oauth
    response = await http.post(
        "/register",
        json={"redirect_uris": ["https://evil.example/cb"], "token_endpoint_auth_method": "none"},
    )
    assert response.status_code == 400


async def test_access_token_carries_the_users_dataverse_key(oauth):
    provider, _ = oauth
    _, tokens = await _sign_in(oauth[1])
    access = await provider.verify_token(tokens["access_token"])
    assert access is not None
    assert access.subject == "@alice"
    assert access.claims["dataverse_key"] == "good-token"
    assert await provider.verify_token("not-a-token") is None


async def test_refresh_rotates_and_rechecks_the_dataverse_token(oauth, dataverse):
    provider, http = oauth
    client_id, tokens = await _sign_in(http)

    refreshed = await http.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
            "client_id": client_id,
        },
    )
    assert refreshed.status_code == 200, refreshed.text
    new = refreshed.json()
    assert await provider.verify_token(tokens["access_token"]) is None
    assert (await provider.verify_token(new["access_token"])).claims["dataverse_key"] == "good-token"

    # Once the user's Dataverse token stops working, refreshing fails so the
    # client sends them back to sign in.
    dataverse.clear()
    refused = await http.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": new["refresh_token"],
            "client_id": client_id,
        },
    )
    assert refused.status_code in (400, 401)
    assert refused.json()["error"] == "invalid_grant"


async def test_tools_reach_dataverse_as_the_signed_in_user(oauth, monkeypatch):
    from fastmcp.client.transports import StreamableHttpTransport
    from fastmcp.utilities.tests import run_server_async

    provider, http = oauth
    _, tokens = await _sign_in(http)
    monkeypatch.setattr(
        api, "get_settings", lambda: Settings(api_key="server-key", _env_file=None)
    )

    server = FastMCP("auth-test", auth=provider)

    @server.tool
    def dataverse_key() -> str:
        return api._api_key()

    async with run_server_async(server) as url:
        bearer = {"Authorization": f"Bearer {tokens['access_token']}"}
        async with Client(StreamableHttpTransport(url, headers=bearer)) as client:
            result = await client.call_tool("dataverse_key", {})
        assert result.data == "good-token"

        async with httpx.AsyncClient() as raw:
            anonymous = await raw.post(url, json={})
        assert anonymous.status_code == 401


CLAUDE_CODE = "https://claude.ai/oauth/claude-code-client-metadata"


async def test_clients_identified_by_metadata_document_url(oauth, monkeypatch):
    # Claude Code skips registration and sends the URL of its client metadata
    # document as client_id, then calls back on a random loopback port.
    from fastmcp.server.auth.cimd import CIMDDocument, CIMDFetcher

    async def fetch(self, url):
        assert url == CLAUDE_CODE
        return CIMDDocument(
            client_id=CLAUDE_CODE,
            client_name="Claude Code",
            redirect_uris=["http://localhost/callback", "http://127.0.0.1/callback"],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            token_endpoint_auth_method="none",
        )

    monkeypatch.setattr(CIMDFetcher, "fetch", fetch)
    _, http = oauth

    metadata = (await http.get("/.well-known/oauth-authorization-server")).json()
    assert metadata["client_id_metadata_document_supported"] is True
    assert "none" in metadata["token_endpoint_auth_methods_supported"]

    redirect = "http://localhost:49152/callback"
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    response = await http.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": CLAUDE_CODE,
            "redirect_uri": redirect,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "st4te",
        },
    )
    assert response.status_code == 302, response.text
    request_id = parse_qs(urlparse(response.headers["location"]).query)["request"][0]
    assert "Claude Code" in (await http.get("/login", params={"request": request_id})).text

    signed_in = await http.post("/login", data={"request": request_id, "token": "good-token"})
    assert signed_in.status_code == 303
    assert signed_in.headers["location"].startswith(redirect)
    code = parse_qs(urlparse(signed_in.headers["location"]).query)["code"][0]
    tokens = await http.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect,
            "client_id": CLAUDE_CODE,
            "code_verifier": verifier,
        },
    )
    assert tokens.status_code == 200, tokens.text


def test_default_redirects_admit_aifindr_hub_only_at_its_callback():
    from fastmcp.server.auth.redirect_validation import validate_redirect_uri

    allowed = Settings(_env_file=None).oauth_allowed_redirect_uris
    assert validate_redirect_uri("https://api.saas.aifindr.ai/mcp/oauth/callback", allowed)
    assert not validate_redirect_uri("https://api.saas.aifindr.ai/elsewhere", allowed)
    assert not validate_redirect_uri("https://evil.example/mcp/oauth/callback", allowed)
