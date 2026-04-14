"""API service configuration.

Uses Pydantic Settings v2. Fields that don't belong to the API namespace
(DATABASE_URL, REDIS_URL, etc.) use `validation_alias` to read the
unprefixed env var — the global `env_prefix` only applies when no alias
is declared.
"""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # populate_by_name lets tests/fixtures still pass values by the Python
    # field name (e.g. Settings(database_url="…")) even though env loading
    # uses the explicit alias.
    model_config = SettingsConfigDict(
        env_prefix="API_",
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    # App-scoped settings (read from API_*)
    debug: bool = Field(default=False)
    secret_key: SecretStr = Field(description="JWT signing secret")
    cors_origins: list[str] = Field(default=["http://localhost:3000"])
    rate_limit_per_minute: int = Field(default=60)

    # Shared infrastructure (read from unprefixed env vars)
    database_url: str = Field(default="", validation_alias="DATABASE_URL")
    redis_url: str = Field(
        default="redis://localhost:6379/0", validation_alias="REDIS_URL"
    )
    celery_broker_url: str = Field(
        default="redis://localhost:6379/0", validation_alias="CELERY_BROKER_URL"
    )

    # Brain API (url is per-API, key is shared secret)
    brain_api_url: str = Field(default="http://localhost:8020")
    brain_api_key: str = Field(default="", validation_alias="BRAIN_API_KEY")

    # Internal API key for service-to-service callbacks (worker → api).
    # Separate from brain_api_key so compromise of one doesn't grant the other.
    internal_api_key: str = Field(default="", validation_alias="INTERNAL_API_KEY")

    # Keycloak (shared identity provider config)
    keycloak_url: str = Field(default="", validation_alias="KEYCLOAK_URL")
    keycloak_realm: str = Field(default="redarch", validation_alias="KEYCLOAK_REALM")
    keycloak_client_id: str = Field(
        default="redarch-km", validation_alias="KEYCLOAK_CLIENT_ID"
    )

    # Observability (shared)
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")

    # E2E test mode (dev-only)
    e2e_test_mode: bool = Field(
        default=False,
        description=(
            "When true, API accepts an X-Test-User header in place of Keycloak JWTs. "
            "NEVER enable in production."
        ),
    )
    e2e_test_secret: SecretStr = Field(
        default=SecretStr(""),
        description="Shared secret required alongside X-Test-User; prevents abuse.",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide Settings singleton (thread-safe via lru_cache)."""
    return Settings()  # type: ignore[call-arg]
