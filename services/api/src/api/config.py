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

    # Enterprise API (/api/v1, authenticated by org API keys).
    # Per-key request quota, enforced across workers via Redis. Distinct env var
    # from the legacy API_RATE_LIMIT_PER_MINUTE (which feeds rate_limit_per_minute
    # above) so the two limits don't collide on one variable.
    api_rate_limit_per_minute: int = Field(default=600, validation_alias="API_KEY_RATE_LIMIT_PER_MINUTE")
    # Coarse per-client-IP quota applied BEFORE key resolution, so a flood of
    # invalid/unknown keys can't hammer the auth lookup unbounded. Generous by
    # design (a legitimate high-throughput client behind one IP must not trip it).
    api_ip_rate_limit_per_minute: int = Field(
        default=1200, validation_alias="API_IP_RATE_LIMIT_PER_MINUTE"
    )
    # Whether to serve the public API docs (/api/v1/docs).
    # On by default; set false to hide the interactive docs in a hardened deploy.
    api_docs_enabled: bool = Field(default=True, validation_alias="API_DOCS_ENABLED")

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
    # A smaller/cheaper/faster model for short auxiliary calls (e.g. the workflow
    # `summarize` action that compresses a RAG answer into one spoken line for a
    # robot). Falls back to the chat model if the env var is unset.
    openai_summary_model: str = Field(default="gpt-5-nano", validation_alias="OPENAI_SUMMARY_MODEL")

    # Additional LLM providers for the multi-provider agent org (services/agents/).
    # Each central key is a fallback; an org's own key (org_provider_credentials)
    # takes precedence, mirroring the openai_api_key convention above. Model ids
    # are LiteLLM-format ("<provider>/<model>"); see services/agents/llm/catalog.py.
    anthropic_api_key: SecretStr = Field(default=SecretStr(""), validation_alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(default="anthropic/claude-sonnet-5", validation_alias="ANTHROPIC_CHAT_MODEL")
    gemini_api_key: SecretStr = Field(default=SecretStr(""), validation_alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini/gemini-2.5-pro", validation_alias="GEMINI_CHAT_MODEL")

    # Agent runtime budgets + escalation backstops (services/agents/). The
    # iteration cap mirrors the config assistant's MAX_ITERATIONS; the escalation
    # timers auto-bubble a stalled escalation to a human (see services/agents/notify.py).
    agent_max_iterations: int = Field(default=32, validation_alias="AGENT_MAX_ITERATIONS")
    agent_run_concurrency: int = Field(default=4, validation_alias="AGENT_RUN_CONCURRENCY")
    agent_escalation_timeout_seconds: int = Field(
        default=2700, validation_alias="AGENT_ESCALATION_TIMEOUT_SECONDS"
    )
    agent_supervisor_idle_seconds: int = Field(
        default=1200, validation_alias="AGENT_SUPERVISOR_IDLE_SECONDS"
    )
    # Default recipient for bubbled escalations/approvals when no org admin email
    # resolves; empty means fall back to the org admins only.
    agent_notify_email: str = Field(default="", validation_alias="AGENT_NOTIFY_EMAIL")

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

    # Local hosts the workflow HTTP actions may reach even though they resolve to
    # a private/loopback address — e.g. a robot-control bridge on localhost/LAN.
    # A host listed here passes the allow-list check AND bypasses the private-IP
    # SSRF guard; it is matched EXACTLY against the request host. Comma-separated;
    # empty (default) keeps the strict deny-by-default guard for every host.
    workflow_trusted_local_hosts_raw: str = Field(
        default="", validation_alias="WORKFLOW_TRUSTED_LOCAL_HOSTS"
    )

    # Global kill-switch for the BPMN token engine. When true (default),
    # schema_version-2 workflows (or any using the new node vocabulary) run on the
    # token engine; legacy v1 workflows always run on the walker regardless. Turn
    # off only to pause v2 execution in an emergency.
    workflow_token_engine_enabled: bool = Field(
        default=True, validation_alias="WORKFLOW_TOKEN_ENGINE_ENABLED"
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

    # Mailpit message-capture API for the site-admin "Sent Emails" console. Mailpit
    # is a dev/staging container (km2_mailpit); in production the API talks to a real
    # SMTP relay and nothing is captured, so the console tolerates this being
    # unreachable. Points at the host mapping (8025) for host-run dev; in-cluster
    # deployments override with http://mailpit:8025.
    mailpit_api_url: str = Field(default="http://localhost:8025", validation_alias="MAILPIT_API_URL")

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

    @property
    def workflow_trusted_local_hosts(self) -> tuple[str, ...]:
        """Local hosts allowed to bypass the private-IP SSRF guard (empty = none)."""
        return tuple(p.strip() for p in self.workflow_trusted_local_hosts_raw.split(",") if p.strip())

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
