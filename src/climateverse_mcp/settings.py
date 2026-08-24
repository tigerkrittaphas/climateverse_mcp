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
    aifindr_knowledge_version: str = Field(
        default="",
        validation_alias="AIFINDR_KNOWLEDGE_VERSION",
    )


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance."""
    return Settings()
