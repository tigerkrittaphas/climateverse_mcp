"""Server configuration, loaded from environment variables (CLIMATEVERSE_*)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Reads CLIMATEVERSE_API_KEY etc. from the environment or a local .env file."""

    model_config = SettingsConfigDict(env_prefix="CLIMATEVERSE_", env_file=".env")

    api_key: str = ""
    api_base_url: str = "https://india.climateverse.net/"


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance."""
    return Settings()
