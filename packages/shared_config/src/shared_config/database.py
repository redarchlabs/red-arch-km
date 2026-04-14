"""PostgreSQL database configuration."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")

    database_url: str = Field(
        description="PostgreSQL async connection URL (postgresql+asyncpg://...)"
    )
    db_pool_size: int = Field(default=10, description="Connection pool size")
    db_max_overflow: int = Field(default=5, description="Max overflow connections")
    db_echo: bool = Field(default=False, description="Echo SQL queries to log")
