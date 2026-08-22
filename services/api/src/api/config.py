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
    cors_origins: list[str] = Field(default=["http://localhost:3002"])
    rate_limit_per_minute: int = Field(default=60)

    # Enterprise API (/api/v1, authenticated by org API keys).
    # Per-key request quota, enforced across workers via Redis. Distinct env var
    # from the legacy API_RATE_LIMIT_PER_MINUTE (which feeds rate_limit_per_minute
    # above) so the two limits don't collide on one variable.
    api_rate_limit_per_minute: int = Field(default=600, validation_alias="API_KEY_RATE_LIMIT_PER_MINUTE")
    # Coarse per-client-IP quota applied BEFORE key resolution, so a flood of
    # invalid/unknown keys can't hammer the auth lookup unbounded. Generous by
    # design (a legitimate high-throughput client behind one IP must not trip it).
    api_ip_rate_limit_per_minute: int = Field(default=1200, validation_alias="API_IP_RATE_LIMIT_PER_MINUTE")
    # Whether to serve the public API docs (/api/v1/docs).
    # On by default; set false to hide the interactive docs in a hardened deploy.
    api_docs_enabled: bool = Field(default=True, validation_alias="API_DOCS_ENABLED")
    # Per-token ceiling for a PUBLICLY SHARED view (/api/public/views/{token}).
    # This one limit is shared by every device on the link, so it has to be sized for
    # the audience, not for one client: a shared page that re-renders every 2s costs 30
    # requests/minute PER PHONE, so a class of thirty needs ~900. The old ceiling of 120
    # meant the FIFTH phone to scan a quiz QR started getting 429s — which reads to the
    # room as "failed to load" on everyone's screen at once. Keep it well above
    # (expected devices x 60000/refresh_ms); it still bounds a leaked link.
    public_view_rate_limit_per_minute: int = Field(default=1200, validation_alias="PUBLIC_VIEW_RATE_LIMIT_PER_MINUTE")

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
    openai_model: str = Field(default="gpt-5.6-luna", validation_alias="OPENAI_CHAT_MODEL")
    # A smaller/cheaper/faster model for short auxiliary calls (e.g. the workflow
    # `summarize` action that compresses a RAG answer into one spoken line for a
    # robot). Falls back to the chat model if the env var is unset.
    openai_summary_model: str = Field(default="gpt-5-nano", validation_alias="OPENAI_SUMMARY_MODEL")
    # Point the OpenAI SDK at an OpenAI-compatible server (Ollama, vLLM, llama.cpp)
    # instead of api.openai.com — the SDK speaks plain HTTP to whatever base_url it is
    # given, so "run the LLM locally" is a deployment setting, not a code change. Empty
    # (the default) preserves today's behaviour exactly, so production is unaffected;
    # the robot's own KM2 instance sets it. A local endpoint needs no credential, so
    # this also makes the API key optional — see api/services/openai_client.py.
    openai_base_url: str = Field(default="", validation_alias="OPENAI_BASE_URL")
    # Per-model overrides of that endpoint: "model=url" pairs, comma or space separated.
    # A self-hosted server serves ONE loaded model, so choosing a model only means
    # something when different ids can reach different processes — e.g. a small fast
    # model for condensing retrieved passages into a spoken line (generation-bound work
    # where a 30B model spends ~1.2s per 10 words) alongside a large one for reasoning.
    # Unset (default): every model goes to openai_base_url, exactly as before.
    openai_model_routes: str = Field(default="", validation_alias="OPENAI_MODEL_ROUTES")

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
    agent_escalation_timeout_seconds: int = Field(default=2700, validation_alias="AGENT_ESCALATION_TIMEOUT_SECONDS")
    # A claimed run whose heartbeat is older than this is presumed orphaned by a dead
    # worker: requeued once, then finalized as an error.
    agent_run_lease_ttl_seconds: int = Field(default=600, validation_alias="AGENT_RUN_LEASE_TTL_SECONDS")
    agent_supervisor_idle_seconds: int = Field(default=1200, validation_alias="AGENT_SUPERVISOR_IDLE_SECONDS")
    # How long the interactive console keeps a stream open waiting for a person to
    # answer an agent's question. Past this the run is handed to the background
    # sweep and the question waits in the inbox — nothing is lost either way, so
    # this trades "answer it right here" against holding an HTTP connection for a
    # tab someone walked away from.
    agent_console_inline_wait_seconds: int = Field(default=300, validation_alias="AGENT_CONSOLE_INLINE_WAIT_SECONDS")
    # How many times one console session will resume inline. An agent that asks
    # question after question must not hold the connection indefinitely.
    agent_console_inline_resumes_max: int = Field(default=8, validation_alias="AGENT_CONSOLE_INLINE_RESUMES_MAX")
    # Default recipient for bubbled escalations/approvals when no org admin email
    # resolves; empty means fall back to the org admins only.
    agent_notify_email: str = Field(default="", validation_alias="AGENT_NOTIFY_EMAIL")

    # Claude Code CLI tool (services/agents/tools/claude_code.py) — lets a single,
    # explicitly-granted agent shell the local Claude Code CLI so the owner's Max plan
    # can do heavy dev/ops work. OFF by default: it runs code on the host, so it is
    # only registered when enabled AND only works where the CLI is installed+authed
    # (the host API process, via the interactive console — never the worker container).
    enable_claude_cli_tool: bool = Field(default=False, validation_alias="CLAUDE_CLI_TOOL_ENABLED")
    # Absolute path to the `claude` binary. No default: the CLI is not on the host
    # PATH in a non-login shell, so the operator must point at the resolved binary.
    claude_cli_path: str = Field(default="", validation_alias="CLAUDE_CLI_PATH")
    # Allow-listed working-directory root. The tool refuses to run outside this dir
    # (and errors if it is unset), bounding what the CLI can read/touch.
    claude_cli_working_dir: str = Field(default="", validation_alias="CLAUDE_CLI_WORKING_DIR")
    # Comma-separated Claude Code tool allow-list passed via --allowedTools. Default
    # is read-only (analysis/answers); widen to Edit/Bash only as a deliberate opt-in.
    claude_cli_allowed_tools: str = Field(
        default="Read,Grep,Glob,WebFetch", validation_alias="CLAUDE_CLI_ALLOWED_TOOLS"
    )
    # Hard timeout for a single CLI invocation; the subprocess is killed on expiry.
    claude_cli_timeout_seconds: int = Field(default=300, validation_alias="CLAUDE_CLI_TIMEOUT_SECONDS")

    # Comma-separated import paths of out-of-tree LLM provider modules, imported at
    # startup so they can register themselves in the provider catalog. Empty (the
    # default) means this deployment offers only what ships here: the official
    # vendor APIs plus any OpenAI-shaped self-hosted server. See
    # api/services/agents/llm/plugins.py for the contract a plugin implements.
    llm_provider_plugins: str = Field(default="", validation_alias="LLM_PROVIDER_PLUGINS")

    # Gemini model used by the web_research tool (Google Search grounding). Flash is
    # cheap + grounding-capable and runs on the free 1,500 grounding-requests/day tier
    # via the AI Studio GEMINI_API_KEY. Overridable for scale/Vertex later.
    agent_web_research_model: str = Field(
        default="gemini/gemini-2.5-flash", validation_alias="AGENT_WEB_RESEARCH_MODEL"
    )
    # Claude model used by web_research when an Anthropic key is available — the
    # preferred backend, because its server-side tools include web_fetch and can open
    # a URL the question names. Must be a model that supports the dynamic-filtering
    # tool versions (Opus 4.6+ / Sonnet 4.6+); an older one is refused by the API.
    agent_web_search_model: str = Field(default="claude-opus-5", validation_alias="AGENT_WEB_SEARCH_MODEL")
    # The acceptance auditor: does the delivered work answer what was asked? One short
    # call per closing order, reading only the request and the result — so a small
    # model is the right default. Set AGENT_ACCEPTANCE_ENFORCE=false to record the
    # verdict without blocking, which is how to try it out on a live org first.
    agent_acceptance_model: str = Field(default="gpt-5.6-luna", validation_alias="AGENT_ACCEPTANCE_MODEL")
    agent_acceptance_enforce: bool = Field(default=True, validation_alias="AGENT_ACCEPTANCE_ENFORCE")
    # Batch single-shot generation (batch_generate tool): how often to poll the
    # Anthropic Message Batch and how long to wait before returning a batch id.
    agent_batch_poll_interval_seconds: int = Field(default=10, validation_alias="AGENT_BATCH_POLL_INTERVAL_SECONDS")
    agent_batch_max_wait_seconds: int = Field(default=180, validation_alias="AGENT_BATCH_MAX_WAIT_SECONDS")

    # Public base URL of THIS API service — used to build the OAuth redirect URI for
    # the MCP "Connect" flow (the provider redirects the browser back to
    # {api_public_url}/api/agents/mcp-servers/oauth/callback). Must be reachable from
    # the user's browser + registered with the provider in production.
    api_public_url: str = Field(default="http://localhost:8000", validation_alias="API_PUBLIC_URL")

    # Application-level encryption secret for per-org third-party credentials at
    # rest (e.g. orgs.openai_api_key). Derives a Fernet key (see services/crypto.py).
    # A dev default keeps local/test envs working; production MUST set the env var.
    org_encryption_key: SecretStr = Field(
        default=SecretStr(_DEV_ORG_ENCRYPTION_KEY), validation_alias="ORG_ENCRYPTION_KEY"
    )

    # Allow-listed webhook hosts for workflow send_webhook actions (SSRF guard).
    # Comma-separated; empty means webhooks are disabled.
    workflow_webhook_allowlist_raw: str = Field(default="", validation_alias="WORKFLOW_WEBHOOK_ALLOWLIST")

    # Local hosts the workflow HTTP actions may reach even though they resolve to
    # a private/loopback address — e.g. a robot-control bridge on localhost/LAN.
    # A host listed here passes the allow-list check AND bypasses the private-IP
    # SSRF guard; it is matched EXACTLY against the request host. Comma-separated;
    # empty (default) keeps the strict deny-by-default guard for every host.
    workflow_trusted_local_hosts_raw: str = Field(default="", validation_alias="WORKFLOW_TRUSTED_LOCAL_HOSTS")

    # Global kill-switch for the BPMN token engine. When true (default),
    # schema_version-2 workflows (or any using the new node vocabulary) run on the
    # token engine; legacy v1 workflows always run on the walker regardless. Turn
    # off only to pause v2 execution in an emergency.
    workflow_token_engine_enabled: bool = Field(default=True, validation_alias="WORKFLOW_TOKEN_ENGINE_ENABLED")

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
    public_base_url: str = Field(default="http://localhost:3002", validation_alias="PUBLIC_BASE_URL")

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

    # Connection-pool budget for this process (see api/db.py). Connections are
    # acquired around a unit of read/write work and released immediately after, so
    # the ceiling bounds *concurrent work*, not concurrent users. Headroom against
    # PostgreSQL's max_connections, which every process shares.
    db_pool_size: int = Field(default=25, validation_alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(default=15, validation_alias="DB_MAX_OVERFLOW")
    # Fail a checkout that waits this long instead of blocking forever — a stalled
    # caller then shows up as a clear error rather than as a hung request.
    db_pool_timeout_seconds: int = Field(default=30, validation_alias="DB_POOL_TIMEOUT_SECONDS")

    # Emails that are site admins the moment they first sign in. A profile row only
    # exists after a successful login, so without this there is no way to authorize
    # an administrator ahead of time — the first-run setup token covers exactly one
    # person, once, and every later admin has to be promoted by an existing one.
    # Additive only: listing an address never demotes anyone, and removing one does
    # not revoke access (use the site-admin console for that).
    site_admin_emails_raw: str = Field(default="", validation_alias="SITE_ADMIN_EMAILS")

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
    def site_admin_emails(self) -> frozenset[str]:
        """Parsed SITE_ADMIN_EMAILS, lower-cased for case-insensitive comparison
        (IdPs vary on the case they assert for the same mailbox)."""
        return frozenset(p.strip().lower() for p in self.site_admin_emails_raw.split(",") if p.strip())

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

    @property
    def claude_cli_allowed_tools_list(self) -> list[str]:
        """Claude Code tool allow-list for the run_claude_code tool (empty = none)."""
        return [p.strip() for p in self.claude_cli_allowed_tools.split(",") if p.strip()]

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
