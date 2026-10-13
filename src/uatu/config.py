"""Settings shared by every Uatu component, read from environment variables (or a .env file)."""

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

    database_url: str = "postgresql+asyncpg://uatu:uatu@localhost:5432/uatu"

    @field_validator("database_url")
    @classmethod
    def _use_asyncpg_driver(cls, url: str) -> str:
        # Accept the plain postgresql:// URLs that psql, QGIS, etc. use.
        for scheme in ("postgresql://", "postgres://"):
            if url.startswith(scheme):
                return "postgresql+asyncpg://" + url.removeprefix(scheme)
        return url
