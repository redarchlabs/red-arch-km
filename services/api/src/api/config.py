"""API service configuration."""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="API_", env_file=".env", extra="ignore")

    # App
    debug: bool = Field(default=False)
    secret_key: SecretStr = Field(description="JWT signing secret")
    cors_origins: list[str] = Field(default=["http://localhost:3000"])
    rate_limit_per_minute: int = Field(default=60)

    # Database (no prefix — shared)
    database_url: str = Field(default="", json_schema_extra={"env": "DATABASE_URL"})

    # Redis
    redis_url: str = Field(default="redis://localhost:6379/0", json_schema_extra={"env": "REDIS_URL"})

    # Celery
    celery_broker_url: str = Field(default="redis://localhost:6379/0", json_schema_extra={"env": "CELERY_BROKER_URL"})

    # Brain API
    brain_api_url: str = Field(default="http://localhost:8020")
    brain_api_key: str = Field(default="", json_schema_extra={"env": "BRAIN_API_KEY"})

    # Keycloak
    keycloak_url: str = Field(default="", json_schema_extra={"env": "KEYCLOAK_URL"})
    keycloak_realm: str = Field(default="redarch", json_schema_extra={"env": "KEYCLOAK_REALM"})
    keycloak_client_id: str = Field(default="redarch-km", json_schema_extra={"env": "KEYCLOAK_CLIENT_ID"})

    # Observability
    log_level: str = Field(default="INFO", json_schema_extra={"env": "LOG_LEVEL"})


def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
