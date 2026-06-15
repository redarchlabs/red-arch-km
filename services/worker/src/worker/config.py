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

    # Callback to the api service for processing_status updates.
    # api_url is where the worker POSTs status; internal_api_key
    # authenticates the request against the api service's internal router.
    api_url: str = Field(default="http://localhost:8000", validation_alias="API_URL")
    internal_api_key: str = Field(default="", validation_alias="INTERNAL_API_KEY")
