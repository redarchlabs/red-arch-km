"""Worker configuration."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")

    brain_api_url: str = Field(default="http://localhost:8020")
    brain_api_key: str = Field(default="", json_schema_extra={"env": "BRAIN_API_KEY"})
    max_file_size_mb: int = Field(default=50)
    openai_api_key: str = Field(default="", json_schema_extra={"env": "OPENAI_API_KEY"})
