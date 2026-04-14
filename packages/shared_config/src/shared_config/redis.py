"""Redis configuration."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RedisSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")

    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL",
    )
