"""Server configuration loaded from ClimateVerse and AIFindr environment variables."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Reads CLIMATEVERSE_API_KEY etc. from the environment or a local .env file."""

    model_config = SettingsConfigDict(
        env_prefix="CLIMATEVERSE_",
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    api_key: str = ""
    api_base_url: str = "https://india.climateverse.net/"
    search_provider: Literal["aifindr", "dataverse"] = "aifindr"
    aifindr_base_url: str = Field(
        default="",
        validation_alias="AIFINDR_BASE_URL",
    )
    aifindr_api_key: str = Field(default="", validation_alias="AIFINDR_API_KEY")
    aifindr_org_id: str = Field(default="", validation_alias="AIFINDR_ORG_ID")
    aifindr_project_id: str = Field(default="", validation_alias="AIFINDR_PROJECT_ID")
    aifindr_search_alpha: float = Field(
        default=0.7,
        ge=0,
        le=1,
        validation_alias="AIFINDR_SEARCH_ALPHA",
    )

    # Hosted deployment. The defaults keep the local stdio server unchanged.
    transport: Literal["stdio", "http"] = "stdio"
    http_host: str = "127.0.0.1"
    http_port: int = 8000
    # Public origin clients reach, e.g. https://mcp.climateverse.net. Required
    # with OAuth: it is baked into the discovery metadata and token audience.
    public_base_url: str = ""
    # Serving over HTTP without OAuth is refused unless explicitly allowed, so a
    # missing secret fails the deploy instead of exposing an open server.
    allow_unauthenticated: bool = False

    # OAuth via AWS Cognito. FastMCP's OAuth proxy fronts the user pool because
    # MCP clients register dynamically and Cognito does not support that.
    cognito_user_pool_id: str = ""
    cognito_region: str = "ap-south-1"
    cognito_client_id: str = ""
    cognito_client_secret: str = ""
    oauth_jwt_signing_key: str = ""
    # Fernet key encrypting client registrations and upstream tokens at rest.
    oauth_storage_key: str = ""
    # DynamoDB table holding OAuth state, so sessions survive task restarts.
    oauth_table: str = ""
    oauth_allowed_redirect_uris: list[str] = [
        "http://localhost/*",
        "http://127.0.0.1/*",
        "https://claude.ai/api/mcp/auth_callback",
        "https://claude.com/api/mcp/auth_callback",
    ]

    # When set, render_report stores reports in S3 and returns a link served by
    # this server instead of writing to the server's own disk.
    reports_bucket: str = ""
@lru_cache
def get_settings() -> Settings:
    """Cached settings instance."""
    return Settings()
