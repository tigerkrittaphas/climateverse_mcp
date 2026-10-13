"""OAuth for the hosted HTTP server: users sign in with their ClimateVerse API token.

Dataverse is not an OAuth provider, so this server is its own authorization
server. MCP clients register and authorize as usual; the authorize step sends
the browser to /login, where the user pastes the API token from their
ClimateVerse account page. The token is checked against Dataverse, then kept
(encrypted at rest) with the access and refresh tokens issued to the client,
and every tool call made with those tokens reaches Dataverse as that user.
"""

import hashlib
import html
import secrets
import time
from typing import Any
from urllib.parse import parse_qs

import httpx
from fastmcp.server.auth import AccessToken, OAuthProvider
from fastmcp.server.auth.redirect_validation import validate_redirect_uri
from key_value.aio.protocols import AsyncKeyValue
from mcp.server.auth.provider import (
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

from climateverse_mcp.settings import Settings

_REQUIRED = (
    ("CLIMATEVERSE_PUBLIC_BASE_URL", "public_base_url"),
    ("CLIMATEVERSE_OAUTH_STORAGE_KEY", "oauth_storage_key"),
    ("CLIMATEVERSE_OAUTH_TABLE", "oauth_table"),
)

# Collection TTLs in seconds. A sign-in page left open longer than
# LOGIN_TTL has to be restarted from the MCP client.
LOGIN_TTL = 10 * 60
CODE_TTL = 5 * 60
ACCESS_TTL = 60 * 60
REFRESH_TTL = 30 * 24 * 60 * 60

# Claim on the AccessToken carrying the user's Dataverse key to the tools.
DATAVERSE_KEY_CLAIM = "dataverse_key"


class AuthConfigError(RuntimeError):
    """The HTTP server cannot start with the configured auth settings."""


def _hash(secret: str) -> str:
    # Storage keys are not encrypted (only values are), so bearer secrets are
    # stored under their hash rather than as themselves.
    return hashlib.sha256(secret.encode()).hexdigest()


class DataverseTokenProvider(OAuthProvider):
    """OAuth authorization server whose login step takes a Dataverse API token."""

    def __init__(
        self,
        *,
        base_url: str,
        dataverse_url: str,
        storage: AsyncKeyValue,
        allowed_redirect_uris: list[str],
    ):
        super().__init__(
            base_url=base_url,
            client_registration_options=ClientRegistrationOptions(enabled=True),
            revocation_options=RevocationOptions(enabled=True),
        )
        self.dataverse_url = dataverse_url.rstrip("/")
        self.storage = storage
        self.allowed_redirect_uris = allowed_redirect_uris

    # Clients

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        record = await self.storage.get(client_id, collection="clients")
        return OAuthClientInformationFull.model_validate(record) if record else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        for uri in client_info.redirect_uris or []:
            if not validate_redirect_uri(uri, self.allowed_redirect_uris):
                raise RegistrationError(
                    error="invalid_redirect_uri",
                    error_description=f"Redirect URI not allowed: {uri}",
                )
        assert client_info.client_id is not None  # assigned by the SDK handler
        await self.storage.put(
            client_info.client_id,
            client_info.model_dump(mode="json", exclude_none=True),
            collection="clients",
        )

    # Authorization: park the request, send the browser to /login

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        if not validate_redirect_uri(params.redirect_uri, self.allowed_redirect_uris):
            raise AuthorizeError(
                error="invalid_request", error_description="Redirect URI not allowed."
            )
        request_id = secrets.token_urlsafe(32)
        await self.storage.put(
            _hash(request_id),
            {"client_id": client.client_id, "params": params.model_dump(mode="json")},
            collection="logins",
            ttl=LOGIN_TTL,
        )
        return f"{str(self.base_url).rstrip('/')}/login?request={request_id}"

    def get_routes(self, mcp_path: str | None = None) -> list[Route]:
        return [
            *super().get_routes(mcp_path),
            Route("/login", self._login, methods=["GET", "POST"]),
        ]

    async def _login(self, request: Request) -> Response:
        if request.method == "POST":
            form = parse_qs((await request.body()).decode())
            request_id = form.get("request", [""])[0]
            token = form.get("token", [""])[0].strip()
        else:
            request_id = request.query_params.get("request", "")
            token = ""

        pending = await self.storage.get(_hash(request_id), collection="logins")
        if pending is None:
            return _page(
                "<h1>This sign-in link has expired</h1>"
                "<p>Start the connection again from your AI client.</p>",
                status=400,
            )
        client = await self.get_client(pending["client_id"])
        client_name = (client.client_name if client else None) or "An MCP client"

        if request.method == "GET":
            return self._form(request_id, client_name)
        if not token or len(token) > 256:
            return self._form(request_id, client_name, "Paste your API token.")
        try:
            user = await self._dataverse_user(token)
        except httpx.HTTPError:
            return self._form(
                request_id,
                client_name,
                "ClimateVerse could not be reached to check the token. Try again shortly.",
            )
        if user is None:
            return self._form(
                request_id,
                client_name,
                "ClimateVerse did not accept that token. Check that you copied all "
                "of it and that it has not expired.",
            )

        # Single use: a replayed form post finds no pending request.
        await self.storage.delete(_hash(request_id), collection="logins")
        params = AuthorizationParams.model_validate(pending["params"])
        code = secrets.token_urlsafe(32)
        authorization_code = AuthorizationCode(
            code=code,
            client_id=pending["client_id"],
            scopes=params.scopes or [],
            expires_at=time.time() + CODE_TTL,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
            subject=user,
        )
        await self.storage.put(
            _hash(code),
            {"code": authorization_code.model_dump(mode="json"), "dataverse_key": token},
            collection="codes",
            ttl=CODE_TTL,
        )
        return RedirectResponse(
            construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state),
            status_code=303,
        )

    def _form(self, request_id: str, client_name: str, error: str = "") -> Response:
        token_page = f"{self.dataverse_url}/dataverseuser.xhtml?selectTab=apiTokenTab"
        notice = f'<p class="error">{html.escape(error)}</p>' if error else ""
        return _page(
            "<h1>Sign in with ClimateVerse</h1>"
            f"<p><strong>{html.escape(client_name)}</strong> wants to use ClimateVerse "
            "with your account. It will see the datasets your account can see.</p>"
            f'<p>Copy the API token from <a href="{html.escape(token_page)}" '
            'target="_blank" rel="noopener">your ClimateVerse account page</a> '
            "(API Token tab; create one if there is none) and paste it here.</p>"
            f"{notice}"
            '<form method="post" action="login">'
            f'<input type="hidden" name="request" value="{html.escape(request_id)}">'
            '<label for="token">API token</label>'
            '<input id="token" name="token" type="password" autocomplete="off" '
            'required autofocus>'
            '<button type="submit">Sign in</button>'
            "</form>",
            status=400 if error else 200,
        )

    async def _dataverse_user(self, token: str) -> str | None:
        """The Dataverse user identifier for `token`, or None if it is refused."""
        async with httpx.AsyncClient(base_url=self.dataverse_url, timeout=15.0) as client:
            response = await client.get(
                "/api/users/:me", headers={"X-Dataverse-key": token}
            )
        if response.status_code in (401, 403):
            return None
        response.raise_for_status()
        return response.json()["data"]["identifier"]

    # Tokens

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        record = await self.storage.get(_hash(authorization_code), collection="codes")
        if record is None:
            return None
        code = AuthorizationCode.model_validate(record["code"])
        if code.client_id != client.client_id or code.expires_at < time.time():
            return None
        return code

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        key = _hash(authorization_code.code)
        record = await self.storage.get(key, collection="codes")
        if record is None:
            raise TokenError("invalid_grant", "Authorization code already used.")
        await self.storage.delete(key, collection="codes")
        return await self._issue(
            authorization_code.client_id,
            authorization_code.scopes,
            authorization_code.subject,
            record["dataverse_key"],
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        record = await self.storage.get(_hash(refresh_token), collection="refresh")
        if record is None or record["client_id"] != client.client_id:
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=record["client_id"],
            scopes=record["scopes"],
            expires_at=record["expires_at"],
            subject=record["subject"],
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        if not set(scopes) <= set(refresh_token.scopes):
            raise TokenError("invalid_scope", "Scopes exceed the original grant.")
        record = await self.storage.get(_hash(refresh_token.token), collection="refresh")
        if record is None:
            raise TokenError("invalid_grant", "Refresh token already used.")
        dataverse_key = record["dataverse_key"]
        # Re-check the Dataverse token so a revoked or expired one sends the
        # user back to sign in instead of failing every tool call. If
        # Dataverse is unreachable, keep the session; tool calls will report it.
        try:
            if await self._dataverse_user(dataverse_key) is None:
                await self.revoke_token(refresh_token)
                raise TokenError(
                    "invalid_grant", "The ClimateVerse API token is no longer valid."
                )
        except httpx.HTTPError:
            pass
        await self.revoke_token(refresh_token)
        return await self._issue(
            refresh_token.client_id, scopes, refresh_token.subject, dataverse_key
        )

    async def _issue(
        self, client_id: str, scopes: list[str], subject: str | None, dataverse_key: str
    ) -> OAuthToken:
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = int(time.time())
        common = {
            "client_id": client_id,
            "scopes": scopes,
            "subject": subject,
            "dataverse_key": dataverse_key,
        }
        await self.storage.put(
            _hash(access),
            {**common, "expires_at": now + ACCESS_TTL, "pair": _hash(refresh)},
            collection="access",
            ttl=ACCESS_TTL,
        )
        await self.storage.put(
            _hash(refresh),
            {**common, "expires_at": now + REFRESH_TTL, "pair": _hash(access)},
            collection="refresh",
            ttl=REFRESH_TTL,
        )
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=ACCESS_TTL,
            refresh_token=refresh,
            scope=" ".join(scopes) or None,
        )

    async def load_access_token(self, token: str) -> AccessToken | None:  # type: ignore[override]
        record = await self.storage.get(_hash(token), collection="access")
        if record is None or record["expires_at"] < time.time():
            return None
        return AccessToken(
            token=token,
            client_id=record["client_id"],
            scopes=record["scopes"],
            expires_at=record["expires_at"],
            subject=record["subject"],
            claims={"sub": record["subject"], DATAVERSE_KEY_CLAIM: record["dataverse_key"]},
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:  # type: ignore[override]
        own, other = (
            ("access", "refresh") if isinstance(token, AccessToken) else ("refresh", "access")
        )
        record: dict[str, Any] | None = await self.storage.get(
            _hash(token.token), collection=own
        )
        await self.storage.delete(_hash(token.token), collection=own)
        if record:
            await self.storage.delete(record["pair"], collection=other)


def _page(body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>Sign in · ClimateVerse</title><style>"
        "body{font-family:system-ui,sans-serif;max-width:32rem;margin:4rem auto;"
        "padding:0 1rem;color:#1d2b24;line-height:1.5}"
        "h1{font-size:1.4rem}label{display:block;font-weight:600;margin-top:1rem}"
        "input[type=password]{width:100%;padding:.5rem;font:inherit;box-sizing:border-box}"
        "button{margin-top:1rem;padding:.5rem 1.25rem;font:inherit;background:#1f6f4a;"
        "color:#fff;border:0;border-radius:4px;cursor:pointer}"
        ".error{color:#a12a2a}"
        "</style></head><body>" + body + "</body></html>",
        status_code=status,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
            "frame-ancestors 'none'",
            "Referrer-Policy": "no-referrer",
        },
    )


def build_auth(settings: Settings) -> OAuthProvider | None:
    """Return the ClimateVerse sign-in provider, or None when explicitly running open.

    Partial configuration is an error rather than a silent fallback to an
    unauthenticated server.
    """
    missing = [env for env, field in _REQUIRED if not getattr(settings, field)]
    if len(missing) == len(_REQUIRED) and settings.allow_unauthenticated:
        return None
    if missing:
        raise AuthConfigError(
            "HTTP transport requires OAuth; missing " + ", ".join(missing)
            + ". Set CLIMATEVERSE_ALLOW_UNAUTHENTICATED=true only for local testing."
        )
    if not settings.api_base_url:
        raise AuthConfigError("Sign-in needs CLIMATEVERSE_API_BASE_URL to check tokens.")

    # Imported lazily: the AWS dependencies ship in the optional `aws` extra.
    from cryptography.fernet import Fernet
    from key_value.aio.stores.dynamodb import DynamoDBStore
    from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

    storage = FernetEncryptionWrapper(
        DynamoDBStore(
            table_name=settings.oauth_table,
            region_name=settings.aws_region,
            # Terraform owns the table; the task role cannot create tables.
            auto_create=False,
        ),
        fernet=Fernet(settings.oauth_storage_key),
    )
    return DataverseTokenProvider(
        base_url=settings.public_base_url,
        dataverse_url=settings.api_base_url,
        storage=storage,
        allowed_redirect_uris=settings.oauth_allowed_redirect_uris,
    )
