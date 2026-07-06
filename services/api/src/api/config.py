"""API service configuration.

Uses Pydantic Settings v2. Fields that don't belong to the API namespace
(DATABASE_URL, REDIS_URL, etc.) use `validation_alias` to read the
unprefixed env var — the global `env_prefix` only applies when no alias
is declared.
"""

import logging
from functools import lru_cache

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Dev/test fallback for ORG_ENCRYPTION_KEY so local envs and the test suite work
# without extra setup. A production deployment MUST override this via the env var
# (see the _warn_org_encryption_key validator below).
_DEV_ORG_ENCRYPTION_KEY = "dev-insecure-org-encryption-key-change-me"


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
    redis_url: str = Field(default="redis://localhost:6379/0", validation_alias="REDIS_URL")
    celery_broker_url: str = Field(default="redis://localhost:6379/0", validation_alias="CELERY_BROKER_URL")

    # Brain API (url is per-API, key is shared secret)
    brain_api_url: str = Field(default="http://localhost:8020")
    brain_api_key: str = Field(default="", validation_alias="BRAIN_API_KEY")

    # OpenAI (the in-API agent's tool-calling loop). The central key is a
    # fallback; an org's own key (orgs.openai_api_key) takes precedence.
    openai_api_key: SecretStr = Field(default=SecretStr(""), validation_alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-5-mini", validation_alias="OPENAI_CHAT_MODEL")

    # Application-level encryption secret for per-org third-party credentials at
    # rest (e.g. orgs.openai_api_key). Derives a Fernet key (see services/crypto.py).
    # A dev default keeps local/test envs working; production MUST set the env var.
    org_encryption_key: SecretStr = Field(
        default=SecretStr(_DEV_ORG_ENCRYPTION_KEY), validation_alias="ORG_ENCRYPTION_KEY"
    )

    # Allow-listed webhook hosts for workflow send_webhook actions (SSRF guard).
    # Comma-separated; empty means webhooks are disabled.
    workflow_webhook_allowlist_raw: str = Field(
        default="", validation_alias="WORKFLOW_WEBHOOK_ALLOWLIST"
    )

    # Internal API key for service-to-service callbacks (worker → api).
    # Separate from brain_api_key so compromise of one doesn't grant the other.
    internal_api_key: str = Field(default="", validation_alias="INTERNAL_API_KEY")

    # Object storage (MinIO / S3-compatible) for uploaded originals. Shared
    # infrastructure, so the env vars are unprefixed (STORAGE_*) and read by
    # both the API and the worker — mirrors the DATABASE_URL/REDIS_URL pattern.
    storage_endpoint: str = Field(default="http://localhost:9000", validation_alias="STORAGE_ENDPOINT")
    storage_access_key: str = Field(default="", validation_alias="STORAGE_ACCESS_KEY")
    storage_secret_key: SecretStr = Field(default=SecretStr(""), validation_alias="STORAGE_SECRET_KEY")
    storage_bucket: str = Field(default="km-documents", validation_alias="STORAGE_BUCKET")
    storage_region: str = Field(default="us-east-1", validation_alias="STORAGE_REGION")

    # Upload size cap; shared with the worker's MAX_FILE_SIZE_MB so both sides
    # agree on the limit (API rejects at the boundary, worker as defense).
    max_file_size_mb: int = Field(default=50, validation_alias="MAX_FILE_SIZE_MB")

    # Clerk (sole IdP). Backends verify the token by its `iss`, which must match
    # clerk_jwt_issuer = Clerk Frontend API URL (the `iss`). CLERK_ALLOWED_AZP is
    # comma-separated to share ONE env format with the Go verifier; see
    # clerk_allowed_azp_list. clerk_secret_key is reserved for Backend-API
    # provisioning (not needed for JWKS verify).
    clerk_jwt_issuer: str = Field(default="", validation_alias="CLERK_JWT_ISSUER")
    clerk_allowed_azp: str = Field(default="", validation_alias="CLERK_ALLOWED_AZP")
    clerk_secret_key: SecretStr = Field(default=SecretStr(""), validation_alias="CLERK_SECRET_KEY")

    # First-run setup token TTL (site-admin bootstrap wizard). Expired token
    # simply means "restart the API to reissue".
    setup_token_ttl_seconds: int = Field(default=86400)

    # Public base URL for user-facing links the backend mints (e.g. intake-form
    # links emailed to external users). Points at the Next.js app, not the API.
    public_base_url: str = Field(default="http://localhost:3000", validation_alias="PUBLIC_BASE_URL")

    # Outbound email (SMTP) for intake-form invitations. Email is disabled unless
    # smtp_host and smtp_from are both set, so dev/test never tries to send.
    smtp_host: str = Field(default="", validation_alias="SMTP_HOST")
    smtp_port: int = Field(default=587, validation_alias="SMTP_PORT")
    smtp_username: str = Field(default="", validation_alias="SMTP_USERNAME")
    smtp_password: SecretStr = Field(default=SecretStr(""), validation_alias="SMTP_PASSWORD")
    smtp_from: str = Field(default="", validation_alias="SMTP_FROM")
    smtp_use_tls: bool = Field(default=True, validation_alias="SMTP_USE_TLS")

    # Observability (shared)
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")

    # E2E test mode (dev-only)
    e2e_test_mode: bool = Field(
        default=False,
        description=(
            "When true, API accepts an X-Test-User header in place of a Clerk JWT. NEVER enable in production."
        ),
    )
    e2e_test_secret: SecretStr = Field(
        default=SecretStr(""),
        description="Shared secret required alongside X-Test-User; prevents abuse.",
    )

    @property
    def clerk_allowed_azp_list(self) -> list[str]:
        """Parse CLERK_ALLOWED_AZP into a trimmed list (mirrors Go comma split)."""
        return [p.strip() for p in self.clerk_allowed_azp.split(",") if p.strip()]

    @property
    def workflow_webhook_allowlist(self) -> tuple[str, ...]:
        """Allow-listed hosts for workflow webhooks (empty tuple = disabled)."""
        return tuple(p.strip() for p in self.workflow_webhook_allowlist_raw.split(",") if p.strip())

    @model_validator(mode="after")
    def _require_azp_when_clerk_enabled(self) -> "Settings":
        """Fail fast when Clerk is enabled without an azp allowlist — without it
        the verify path cannot enforce G-AZP. Mirrors the Go config's
        ErrMissingClerkAllowedAZP startup check."""
        if self.clerk_jwt_issuer and not self.clerk_allowed_azp_list:
            msg = "CLERK_ALLOWED_AZP is required when CLERK_JWT_ISSUER is set"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _warn_org_encryption_key(self) -> "Settings":
        """Warn (don't fail) when the insecure dev ORG_ENCRYPTION_KEY is in use.

        Failing hard would break local dev and tests, which rely on the default.
        In production the operator is expected to set ORG_ENCRYPTION_KEY; this
        warning surfaces the misconfiguration in the logs at startup."""
        if self.org_encryption_key.get_secret_value() == _DEV_ORG_ENCRYPTION_KEY:
            logger.warning(
                "ORG_ENCRYPTION_KEY is unset; using the insecure dev default. "
                "Set ORG_ENCRYPTION_KEY in production to protect per-org secrets at rest."
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide Settings singleton (thread-safe via lru_cache)."""
    return Settings()  # type: ignore[call-arg]
