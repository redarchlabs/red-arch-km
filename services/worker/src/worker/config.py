"""Worker configuration.

All env vars are unprefixed (env_prefix=""). Field names map directly to
upper-case env var names.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    brain_api_url: str = Field(default="http://localhost:8020")
    brain_api_key: str = Field(default="", validation_alias="BRAIN_API_KEY")
    max_file_size_mb: int = Field(default=50)
    openai_api_key: str = Field(default="", validation_alias="OPENAI_API_KEY")
