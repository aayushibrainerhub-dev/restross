from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Restaurant API"
    # Set DATABASE_URL in .env. Example:
    # postgresql+psycopg://user:password@host:5432/restaurant
    database_url: str = "postgresql+psycopg://restaurant:restaurant@localhost:5432/restaurant"
    openrouter_api_key: str = ""
    openrouter_model: str = "openai/gpt-oss-20b"
    debug: bool = True
    api_prefix: str = "/api/v1"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator("debug", mode="before")
    @classmethod
    def parse_debug_mode(cls, value: bool | str) -> bool | str:
        """Allow common deployment labels such as DEBUG=release."""
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"release", "production", "prod"}:
                return False
            if normalized in {"development", "dev"}:
                return True
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
