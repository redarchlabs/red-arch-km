# Deployment

This guide covers deploying the Red Arch Knowledge Management Platform (KM2) — the
Docker Compose stacks, a full environment-variable reference, Clerk setup for
production, database roles and migrations, object storage, Celery scaling, health
checks, and backup/restore. It is for operators standing up a dev, staging, or
production instance. The compose files live in `docker/`; the runtime settings they
feed come from `services/api/src/api/config.py` (the API `Settings` class) and the
root `.env.example`.

## Table of Contents

- [Deployment topology](#deployment-topology)
- [Prerequisites](#prerequisites)
- [Compose stacks (dev vs prod)](#compose-stacks-dev-vs-prod)
- [Environment variable reference](#environment-variable-reference)
- [Database: roles, migrations, RLS](#database-roles-migrations-rls)
- [Clerk configuration (production)](#clerk-configuration-production)
- [Object storage (MinIO / S3)](#object-storage-minio--s3)
- [First-run setup (site admin)](#first-run-setup-site-admin)
- [Celery workers and Beat](#celery-workers-and-beat)
- [Health checks](#health-checks)
- [Backup and restore](#backup-and-restore)
- [TLS and ingress](#tls-and-ingress)
- [Security checklist](#security-checklist)
- [Troubleshooting](#troubleshooting)
- [Known gaps / TODO](#known-gaps--todo)

## Deployment topology

KM2 is four application services plus five infrastructure dependencies. The Python
services are the live/authoritative implementation; a Go rewrite exists in
`docker/docker-compose.go.yml` but is not the production target (see
[ARCHITECTURE.md](ARCHITECTURE.md)).

```
                    ┌────────────────┐
                    │  Reverse proxy │  TLS termination (external, e.g. nginx)
                    │   (ingress)    │
                    └───────┬────────┘
              /             │            /api/
        ┌─────▼─────┐             ┌──────▼──────┐
        │    ui     │  Bearer JWT │     api     │  FastAPI, :8000
        │ Next.js   │────────────▶│  (Clerk)    │
        │  :3000    │             └──┬───────┬──┘
        └───────────┘                │       │  X-Internal-API-Key
                                     │       └──────────────┐
                     ┌───────────────┤                      │
              ┌──────▼──────┐  ┌─────▼─────┐          ┌──────▼──────┐
              │  brain-api  │  │  worker   │◀── beat ─│ celery-beat │
              │   :8020     │  │ (Celery)  │  enqueue └─────────────┘
              └──┬───────┬──┘  └─────┬─────┘
                 │       │           │
         ┌───────▼─┐ ┌───▼───┐  ┌────▼────┐ ┌───────┐ ┌───────┐ ┌───────┐
         │ qdrant  │ │ neo4j │  │postgres │ │ redis │ │ minio │ │  ...  │
         │ vectors │ │ graph │  │  (RLS)  │ │broker │ │  S3   │
         └─────────┘ └───────┘  └─────────┘ └───────┘ └───────┘
```

| Service       | Image (prod default)                    | Port | Role |
|---------------|-----------------------------------------|------|------|
| `api`         | `ghcr.io/redarchlabs/km2-api:2.0.0`     | 8000 | REST API, auth/RBAC, entities, forms/views, workflows, agents, `/api/v1` |
| `brain-api`   | `ghcr.io/redarchlabs/km2-brain-api:2.0.0` | 8020 | Ingest, embeddings, vector search, knowledge graph, RAG chat |
| `worker`      | `ghcr.io/redarchlabs/km2-worker:2.0.0`  | —    | Celery worker: document processing, workflow/agent execution |
| `celery-beat` | `ghcr.io/redarchlabs/km2-worker:2.0.0`  | —    | Celery Beat scheduler (same image, `beat` command) |
| `ui`          | `ghcr.io/redarchlabs/km2-ui:2.0.0`      | 3000 | Next.js frontend |
| `postgres`    | `postgres:18`                           | 5432 | Primary store with Row-Level Security |
| `redis`       | `redis:7.4-alpine`                      | 6379 | Celery broker/result backend + rate-limit counters |
| `qdrant`      | `qdrant/qdrant:v1.12.4`                  | 6333 | Vector store (chunks/documents collections) |
| `neo4j`       | `neo4j:5.25.1`                          | 7474/7687 | Knowledge graph (APOC plugin) |
| `minio`       | `minio/minio` (dev stack only)          | 9000/9001 | S3-compatible object storage for uploaded originals |

Image tags default to `${TAG:-2.0.0}` and the registry to
`${REGISTRY:-ghcr.io/redarchlabs}` in `docker/docker-compose.prod.yml`; override both
via env.

## Prerequisites

- Docker Engine + Docker Compose v2.
- PostgreSQL 18 (bundled, or a managed instance — RLS support required).
- Redis 7.4+.
- Qdrant 1.12.x.
- Neo4j 5.25.x with the APOC plugin.
- An S3-compatible object store (bundled MinIO for dev; managed S3/MinIO for prod).
- A Clerk application (cloud OIDC) with a `redarch-km` JWT template — see
  [Clerk configuration](#clerk-configuration-production) and `AUTHENTICATION.md`.
- At least one LLM provider key (OpenAI is the baseline; Anthropic/Gemini optional).

## Compose stacks (dev vs prod)

The `docker/` directory holds several composable files. `docker-compose.yml` is the
canonical full stack; the others include or replace it. For the local hybrid
host/Docker workflow (running services on the host against Docker infra), see
[DEVELOPMENT.md](DEVELOPMENT.md).

| File | Purpose | Entry command |
|------|---------|---------------|
| `docker-compose.infra.yml` | Infra only: postgres, redis, qdrant, neo4j, minio, `createbuckets`, mailpit | `make dev-infra` |
| `docker-compose.yml` | Full stack (app services + infra via `include`) | `docker compose -f docker/docker-compose.yml up -d` |
| `docker-compose.dev.yml` | Additive dev override: source bind-mounts, `uvicorn --reload`, `API_DEBUG=true` | `make dev` |
| `docker-compose.prod.yml` | Standalone production template: pinned tags, `restart: always`, resource limits, strict healthchecks, no bind-mounts, no dev tools | see below |
| `docker-compose.go.yml` | In-progress Go rewrite (not the production target) | `make dev-go` |

Dev vs prod differences:

| Feature | Dev (`docker-compose.yml` / `.dev.yml`) | Prod (`docker-compose.prod.yml`) |
|---------|------------------------------------------|----------------------------------|
| Source mounts / hot reload | Yes (`.dev.yml`) | No |
| Debug mode | `API_DEBUG=true` | `API_DEBUG=false` |
| Image tags | Built locally | Pinned `${REGISTRY}/…:${TAG}` |
| Restart policy | `unless-stopped` (infra) | `restart: always` (all) |
| Resource limits | None | `deploy.resources` limits + reservations |
| Missing-secret behavior | Some `${VAR:?}` guards | Required secrets use `${VAR:?}` — stack refuses to start if unset |
| Dev tools (pgAdmin, Flower, Mailpit, MinIO) | Included | Omitted (bring your own S3 / mail relay) |
| Postgres host port | `5433:5432` (avoids clashes) | `5432` internal only |

Start the production stack (supply a production `.env` or export the required vars):

```bash
docker compose -f docker/docker-compose.prod.yml --env-file .env.prod up -d
```

The prod template refuses to start unless these are set (via `${VAR:?}` guards):
`POSTGRES_PASSWORD`, `APP_DB_PASSWORD`, `NEO4J_PASSWORD`, `API_SECRET_KEY`,
`BRAIN_API_KEY`, `INTERNAL_API_KEY`, `OPENAI_API_KEY`. The template is deliberately
minimal — it does **not** ship a reverse proxy or a MinIO service; layer those in
with your own override file or point `STORAGE_*` at managed S3 (see
[Object storage](#object-storage-minio--s3)).

## Environment variable reference

Copy `.env.example` to `.env` and fill in the values. Secrets are supplied through
the environment / an env file (or a secret manager that populates them); nothing is
hardcoded. Values marked **Required** must be set for a production start — the API
`Settings` validators (`config.py`) fail fast on some (Clerk azp), warn on others
(dev encryption key), and the prod compose `${VAR:?}` guards block the rest.

Unless a `validation_alias` is noted, API-owned settings read the `API_`-prefixed env
var (`env_prefix="API_"` in `config.py`).

### PostgreSQL and the runtime role

| Variable | Purpose | Required |
|----------|---------|----------|
| `POSTGRES_USER` | Superuser/owner; runs Alembic migrations. Default `redarch` | No (default) |
| `POSTGRES_PASSWORD` | Superuser password | **Yes** |
| `POSTGRES_DB` | Database name. Default `redarch_km` | No (default) |
| `DATABASE_URL` | App connection string. Connects as the **non-superuser `km_app`** role so RLS is enforced (`postgresql+asyncpg://km_app:…@postgres:5432/…`) | **Yes** |
| `APP_DB_USER` / `APP_DB_PASSWORD` | Runtime role name/password the prod compose injects into `DATABASE_URL`. Default user `km_app` | **Yes** (prod) |

### Redis and Celery

| Variable | Purpose | Required |
|----------|---------|----------|
| `REDIS_URL` | Redis for rate-limit counters and setup token. Default `redis://redis:6379/0` | No (default) |
| `CELERY_BROKER_URL` | Celery broker. Default `redis://redis:6379/0` | No (default) |
| `CELERY_RESULT_BACKEND` | Celery result backend. Default `redis://redis:6379/1` | No (default) |

### Qdrant and Neo4j (brain-api)

| Variable | Purpose | Required |
|----------|---------|----------|
| `QDRANT_URL` | Qdrant HTTP endpoint. Default `http://qdrant:6333` | No (default) |
| `QDRANT_API_KEY` | Set only for Qdrant Cloud | No |
| `NEO4J_URI` | Bolt URI. Default `bolt://neo4j:7687` | No (default) |
| `NEO4J_USER` | Neo4j user. Default `neo4j` | No (default) |
| `NEO4J_PASSWORD` | Neo4j password | **Yes** |

### Object storage (MinIO / S3)

| Variable | Purpose | Required |
|----------|---------|----------|
| `STORAGE_ENDPOINT` | S3 API URL. Containers use `http://minio:9000`; host processes `http://localhost:9000`. Host **must not** contain an underscore (botocore rejects it) | **Yes** |
| `STORAGE_ACCESS_KEY` | S3/MinIO access key | **Yes** |
| `STORAGE_SECRET_KEY` | S3/MinIO secret key | **Yes** |
| `STORAGE_BUCKET` | Bucket for originals. Default `km-documents`; auto-created by `createbuckets` in dev | No (default) |
| `STORAGE_REGION` | S3 region. Default `us-east-1` | No (default) |
| `MAX_FILE_SIZE_MB` | Upload cap (API rejects at the boundary). Default `50` | No (default) |

### Clerk — backend verifier

| Variable | Purpose | Required |
|----------|---------|----------|
| `CLERK_JWT_ISSUER` | Clerk Frontend API URL; the token `iss` must match it | **Yes** (prod auth) |
| `CLERK_ALLOWED_AZP` | Comma-separated UI-origin allowlist. Clerk session tokens carry no `aud`, so the verifier enforces `azp` against this list. Startup **fails** if `CLERK_JWT_ISSUER` is set but this is empty. Match the origin byte-for-byte | **Yes** (with Clerk) |
| `CLERK_SECRET_KEY` | Clerk Backend API secret (`sk_…`). Used by `@clerk/nextjs` server-side (middleware/SSR) and reserved for Backend-API provisioning — **not** needed for JWKS verification | Yes (UI) |

### Clerk — UI (`@clerk/nextjs`)

| Variable | Purpose | Required |
|----------|---------|----------|
| `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` | Publishable key (`pk_…`), browser-exposed | **Yes** |
| `NEXT_PUBLIC_CLERK_SIGN_IN_URL` | Sign-in route. Default `/login` | No (default) |
| `NEXT_PUBLIC_CLERK_SIGN_UP_URL` | Sign-up route. Default `/sign-up` | No (default) |
| `NEXT_PUBLIC_CLERK_JWT_TEMPLATE` | JWT template name. Default `redarch-km` | No (default) |

> The `NEXT_PUBLIC_*` Clerk values are **build-time** args for the UI image (Next.js
> inlines them) and are also read at runtime. When building the prod UI image, pass
> them as build args, not just runtime env.

### API service

| Variable | Purpose | Required |
|----------|---------|----------|
| `API_SECRET_KEY` | JWT signing secret (random) | **Yes** |
| `API_DEBUG` | Debug mode. Default `false` | No |
| `API_CORS_ORIGINS` | JSON array of allowed origins. Default `["http://localhost:3000"]` | No (default) |
| `API_RATE_LIMIT_PER_MINUTE` | Per-session request quota. Default `60` | No (default) |
| `API_PUBLIC_URL` | Public URL of this API; builds the MCP OAuth redirect URI. Default `http://localhost:8000` | Yes (MCP) |
| `API_BRAIN_API_URL` | Where the API reaches brain-api. Default `http://localhost:8020`; set `http://brain-api:8020` in-cluster | Yes (in-cluster) |

### Enterprise API (`/api/v1`, org API keys)

| Variable | Purpose | Required |
|----------|---------|----------|
| `API_KEY_RATE_LIMIT_PER_MINUTE` | Per-key request quota (Redis). Default `600` | No (default) |
| `API_IP_RATE_LIMIT_PER_MINUTE` | Coarse per-client-IP pre-auth flood guard. Default `1200` | No (default) |
| `API_DOCS_ENABLED` | Serve `/api/v1/docs`. Default `true`; set `false` to hide in hardened prod | No (default) |

### Brain API

| Variable | Purpose | Required |
|----------|---------|----------|
| `BRAIN_API_KEY` | Shared secret for API↔brain-api service auth | **Yes** |
| `BRAIN_CHUNK_COLLECTION_SUFFIX` | Qdrant chunk collection suffix. Default `chunks` | No (default) |
| `BRAIN_DOCUMENT_COLLECTION_SUFFIX` | Qdrant document collection suffix. Default `documents` | No (default) |
| `BRAIN_MAX_TOKENS` | Ingest token ceiling. Default `16000` | No (default) |

### Internal API (worker → api)

| Variable | Purpose | Required |
|----------|---------|----------|
| `INTERNAL_API_KEY` | Shared secret for worker→api callbacks (`X-Internal-API-Key`). Separate from `BRAIN_API_KEY`; api rejects internal callbacks when empty | **Yes** |
| `API_URL` | Where the worker POSTs callbacks. Default `http://api:8000` | No (default) |

### LLM providers

Central keys are fallbacks; per-org keys (encrypted at rest — migration 029
`org_provider_credentials`, and `orgs.openai_api_key`) take precedence.

| Variable | Purpose | Required |
|----------|---------|----------|
| `OPENAI_API_KEY` | OpenAI key (agent loop, embeddings, chat). Central fallback | **Yes** |
| `OPENAI_CHAT_MODEL` | Default `gpt-5-mini` | No (default) |
| `OPENAI_EMBEDDING_MODEL` | Default `text-embedding-3-small` | No (default) |
| `OPENAI_OCR_MODEL` | Vision model for the `ai` upload extraction. Default `gpt-4.1-mini` | No (default) |
| `OPENAI_SUMMARY_MODEL` | Small model for short auxiliary calls. Default `gpt-5-nano` | No (default) |
| `ANTHROPIC_API_KEY` | Anthropic key for the multi-provider agent org | No |
| `ANTHROPIC_CHAT_MODEL` | LiteLLM id. Default `anthropic/claude-sonnet-5` | No (default) |
| `GEMINI_API_KEY` | Gemini key (agent org + `web_research` grounding) | No |
| `GEMINI_CHAT_MODEL` | LiteLLM id. Default `gemini/gemini-2.5-pro` | No (default) |

### Agent runtime (`services/agents/`)

| Variable | Purpose | Required |
|----------|---------|----------|
| `AGENT_MAX_ITERATIONS` | Agent tool-loop cap. Default `32` | No (default) |
| `AGENT_RUN_CONCURRENCY` | Concurrent background agent runs. Default `4` | No (default) |
| `AGENT_ESCALATION_TIMEOUT_SECONDS` | Auto-bubble a stalled escalation to a human. Default `2700` | No (default) |
| `AGENT_SUPERVISOR_IDLE_SECONDS` | Supervisor idle backstop. Default `1200` | No (default) |
| `AGENT_NOTIFY_EMAIL` | Fallback escalation recipient (else org admins). Default empty | No |
| `AGENT_WEB_RESEARCH_MODEL` | Gemini model for `web_research`. Default `gemini/gemini-2.5-flash` | No (default) |
| `AGENT_BATCH_POLL_INTERVAL_SECONDS` / `AGENT_BATCH_MAX_WAIT_SECONDS` | Anthropic Message Batch polling. Defaults `10` / `180` | No (default) |

### Claude Code CLI tool (optional, off by default)

Lets one explicitly-granted agent shell the local `claude` CLI. It runs code on the
host, so it only registers when enabled and only works in the host API process
(never the worker container).

| Variable | Purpose | Required |
|----------|---------|----------|
| `CLAUDE_CLI_TOOL_ENABLED` | Master switch. Default `false` | No |
| `CLAUDE_CLI_PATH` | Absolute path to the `claude` binary (no default) | If enabled |
| `CLAUDE_CLI_WORKING_DIR` | Allow-listed working-directory root; tool refuses to run outside it | If enabled |
| `CLAUDE_CLI_ALLOWED_TOOLS` | Comma-separated `--allowedTools`. Default `Read,Grep,Glob,WebFetch` (read-only) | No (default) |
| `CLAUDE_CLI_TIMEOUT_SECONDS` | Hard per-invocation timeout. Default `300` | No (default) |

### Encryption, workflow security, email, setup

| Variable | Purpose | Required |
|----------|---------|----------|
| `ORG_ENCRYPTION_KEY` | Fernet key for per-org third-party secrets at rest. Dev default warns at startup; production **must** set a unique 32+ char random value | **Yes** (prod) |
| `WORKFLOW_WEBHOOK_ALLOWLIST` | Comma-separated hosts for `send_webhook` (SSRF guard). Empty = webhooks disabled | No |
| `WORKFLOW_TRUSTED_LOCAL_HOSTS` | Exact hosts allowed to bypass the private-IP SSRF guard (e.g. a LAN robot bridge). Empty = strict deny | No |
| `WORKFLOW_TOKEN_ENGINE_ENABLED` | Kill-switch for the BPMN token engine. Default `true` | No (default) |
| `PUBLIC_BASE_URL` | Public UI URL used to mint user-facing links (intake forms). Points at the Next.js app | **Yes** |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USERNAME` / `SMTP_PASSWORD` / `SMTP_FROM` / `SMTP_USE_TLS` | Outbound SMTP for intake invitations. Disabled unless `SMTP_HOST` **and** `SMTP_FROM` are both set | No |
| `MAILPIT_API_URL` | Mailpit capture API for the Site Admin "Sent Emails" console (dev/staging). In-cluster: `http://mailpit:8025` | No |
| `API_SETUP_TOKEN_TTL_SECONDS` | First-run setup-token TTL. Default `86400` | No (default) |

### Observability and Celery/Beat tuning

| Variable | Purpose | Required |
|----------|---------|----------|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OTLP collector endpoint | No |
| `OTEL_SERVICE_NAME` | Service name. Default `red-arch-km` | No |
| `LOG_LEVEL` | `DEBUG`/`INFO`/`WARNING`/`ERROR`. Default `INFO` | No (default) |
| `WORKER_CONCURRENCY` | Prod worker `--concurrency`. Default `4` | No (default) |
| `WORKFLOW_SWEEP_INTERVAL` / `WORKFLOW_TIMER_INTERVAL` / `WORKFLOW_TOKEN_INTERVAL` / `WORKFLOW_PARTITION_INTERVAL` | Beat cadences (seconds). Defaults `10` / `30` / `10` / `86400` | No (default) |
| `AGENT_RUN_INTERVAL` / `AGENT_SCHEDULE_INTERVAL` / `BEAT_HEARTBEAT_INTERVAL` | Beat cadences (seconds). Defaults `10` / `30` / `15` | No (default) |

### Dev-only (never enable in production)

`PGADMIN_DEFAULT_EMAIL` / `PGADMIN_DEFAULT_PASSWORD` (pgAdmin), `FLOWER_BASIC_AUTH`
(Flower), and `API_E2E_TEST_MODE` / `API_E2E_TEST_SECRET` (accepts an `X-Test-User`
header in place of a Clerk JWT — an auth bypass; must stay unset in production).

## Database: roles, migrations, RLS

### Roles

`docker/init-db.sql` runs on first Postgres init and creates three roles:

| Role | Privileges | Used for |
|------|-----------|----------|
| `app_user` | Login, per-table CRUD grants, RLS-enforced | Base grants inherited by `km_app` |
| `app_admin` | Login, `BYPASSRLS` | Legacy admin path |
| `km_app` | Login, member of `app_user`, `CREATE` on schema `public` | **The runtime app role** — `DATABASE_URL` connects as this so RLS is actually enforced |

The application connects as the non-superuser `km_app`; cross-org / no-tenant paths
opt into visibility via the `app.bypass` GUC and the `admin_bypass_all` policy
(migration 034), not via role privileges. Migration 035 (`grant_km_app_role`) wires
the grants. On a managed Postgres where you cannot run `init-db.sql`, create `km_app`
and grant it `app_user` + `CREATE ON SCHEMA public` in a bootstrap step, then set the
runtime `DATABASE_URL` to use it. See [DATABASE.md](DATABASE.md) and
[RBAC.md](RBAC.md) for the full RLS model.

### Migrations

Alembic migrations live in `services/api/alembic/versions/` and run **through
`039_entity_access_control`** (028 api_keys → 039). Migrations must run as the admin
owner (`POSTGRES_USER`), **not** as `km_app`:

```bash
# From the repo root — runs alembic against the local infra DB on host port 5433
make migrate

# Equivalent, explicit:
cd services/api
DATABASE_URL=postgresql+asyncpg://$POSTGRES_USER:$POSTGRES_PASSWORD@<host>:5432/$POSTGRES_DB \
  uv run alembic upgrade head
```

Run migrations once per deploy before (or during) rollout; they are the source of
truth for schema, RLS policies, and the workflow partition function. Change
management / release promotion (migrations 037–038) is documented in
[CHANGE_MANAGEMENT.md](CHANGE_MANAGEMENT.md).

## Clerk configuration (production)

KM2 uses Clerk (cloud OIDC) as the sole identity provider. The backend verifies each
request's session token by its issuer against `CLERK_JWT_ISSUER` and enforces `azp`
against `CLERK_ALLOWED_AZP`. Full detail lives in `AUTHENTICATION.md`; the
deploy-critical steps:

1. **Create the application** in the Clerk Dashboard and choose sign-in methods.
2. **Set sign-in / sign-up URLs** to your UI origin (`/login`, `/sign-up`).
3. **Create a JWT template named `redarch-km`** that emits exactly:

   ```json
   {
     "email": "{{user.primary_email_address}}",
     "email_verified": "{{user.email_verified}}",
     "username": "{{user.username}}"
   }
   ```

   The `email_verified` shortcode must be exactly `{{user.email_verified}}`.
   `{{user.primary_email_address_verified}}` is **not** a valid shortcode — it renders
   literally, so `email_verified` never equals `true` and the backend 403-locks out
   every migrated user on first login. The template must emit `email`,
   `email_verified`, **and** `username`: omitting `email` causes silent membership
   loss; omitting `email_verified` blocks the verified-email relink.
4. **Set the allowed origins.** `CLERK_ALLOWED_AZP` must match the UI origin
   byte-for-byte (scheme+host, no trailing slash, port only if the origin has one).
   A mismatch fails closed and rejects all Clerk tokens. Set it to the production UI
   origin (e.g. `https://app.example.com`).
5. **Wire the keys:** `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` (`pk_…`) for the UI,
   `CLERK_SECRET_KEY` (`sk_…`) for `@clerk/nextjs` server code, `CLERK_JWT_ISSUER` =
   your Clerk Frontend API URL.

On first API login, user profiles are auto-provisioned from the JWT.

## Object storage (MinIO / S3)

Uploaded document originals (PDF, images, `.docx`, `.md`, `.txt`) are kept in an
S3-compatible bucket: the API writes the original on upload, the worker downloads it
for OCR/extraction, and the reader serves it back (text inline; PDFs/images via a
short-lived presigned URL). Config is the `STORAGE_*` vars above.

- **Dev:** `docker-compose.infra.yml` runs a `minio` service plus a one-shot,
  idempotent `createbuckets` container that creates `STORAGE_BUCKET` once MinIO is
  healthy (MinIO does not auto-create buckets — without it the first upload fails
  with `NoSuchBucket`). The console is on `:9001`.
- **Prod:** `docker-compose.prod.yml` ships **no** MinIO service. Point `STORAGE_*`
  at managed S3 (or a MinIO you run/override in), and create the bucket via your
  provider. Never use a `STORAGE_ENDPOINT` host containing an underscore — botocore
  raises `Invalid endpoint`.
- **Extraction binaries** live in the worker image (`tesseract-ocr` +
  `poppler-utils` for OCR, `mammoth` for `.docx`→Markdown).
- **Backup:** the bucket holds the only copy of uploaded originals — include it in
  backups (see [Backup and restore](#backup-and-restore)).

## First-run setup (site admin)

The first site admin is claimed through the built-in setup wizard — no manual SQL.
`POST /api/setup/claim` requires a signed-in Clerk user plus a one-time setup token.

1. Start the API with no site admin in the DB. It prints a one-time **setup token**
   (valid `API_SETUP_TOKEN_TTL_SECONDS`, single use) to its logs.
2. Sign in to the UI with the Clerk account that should be the global administrator;
   you are redirected to `/setup`.
3. Paste the token. Your account gets `is_site_admin = true` and the wizard creates
   the first organization.

The token is stored only as a SHA-256 hash in Redis, is single-use, and survives API
restarts until claimed or expired. Once a site admin exists the wizard is disabled
(`POST /api/setup/claim` returns 409). The plaintext token is printed to stdout — if
logs ship to a central aggregator, shorten the TTL for the bootstrap or redact that
line. Fallback for a broken Redis / air-gapped debug: flip the flag directly with
`UPDATE user_profiles SET is_site_admin = true WHERE auth_subject = '<clerk-sub>'`.
See [SITE_ADMIN.md](SITE_ADMIN.md).

## Celery workers and Beat

The document pipeline, the workflow engine, and agent schedules are all poll-based
and driven by Celery. **Celery Beat is mandatory** — without a running beat, the
workflow outbox is never swept, delayed/scheduled workflows never fire, and agent
cron schedules never run.

### Beat schedule

Beat enqueues these periodic tasks (source of truth:
`services/worker/src/worker/celery_app.py`, `app.conf.beat_schedule`). The worker
executes them, calling the API's `/api/internal/*` endpoints
(`services/api/src/api/routers/internal.py`) where cross-service work is needed.

| Task | Default interval | Purpose |
|------|------------------|---------|
| `workflow-sweep-outbox` | 10s | Drain the `workflow_outbox` (change-triggered workflows) |
| `workflow-run-timers` | 30s | Resume due delayed runs + fire due scheduled workflows |
| `workflow-advance-tokens` | 10s | BPMN token engine: reactivate parked tokens, drain the active queue |
| `workflow-maintain-partitions` | 86400s (daily) | Pre-create monthly partitions via `workflow_ensure_partitions()` |
| `agents-advance-runs` | 10s | Claim + drive queued agent runs |
| `agents-run-schedules` | 30s | Fire due cron-triggered agent schedules |
| `beat-heartbeat` | 15s | Liveness beacon for the Site Admin console |

Each interval is overridable via env (`WORKFLOW_SWEEP_INTERVAL`,
`WORKFLOW_TIMER_INTERVAL`, `WORKFLOW_TOKEN_INTERVAL`, `WORKFLOW_PARTITION_INTERVAL`,
`AGENT_RUN_INTERVAL`, `AGENT_SCHEDULE_INTERVAL`, `BEAT_HEARTBEAT_INTERVAL`).

### Partition maintenance (after an upgrade)

The workflow tables (`workflow_outbox`, `workflow_runs`, `workflow_run_steps`) are
RANGE-partitioned by `created_at` (monthly). Beat keeps partitions ahead; to backfill
after an upgrade, call the function directly or the internal endpoint:

```bash
# psql
SELECT workflow_ensure_partitions(2);

# or via the internal endpoint (requires INTERNAL_API_KEY)
curl -X POST -H "X-Internal-API-Key: ${INTERNAL_API_KEY}" \
  "http://api:8000/api/internal/workflows/maintain-partitions?months_ahead=2"
```

### Scaling and HA

- **Workers scale horizontally.** Increase `WORKER_CONCURRENCY` per worker and/or run
  more worker replicas: `docker compose -f docker/docker-compose.prod.yml up -d
  --scale worker=4`.
- **Run exactly one Beat.** Beat is a singleton scheduler — two beats double-fire
  every schedule. Do not scale `celery-beat` above 1; give it `restart: always`.
- `api`, `brain-api`, and `ui` are stateless and scale horizontally behind the
  reverse proxy.

| Component | Scaling strategy |
|-----------|------------------|
| PostgreSQL | Read replicas, connection pooling (PgBouncer) |
| Redis | Redis Cluster or Sentinel |
| Qdrant | Distributed mode with sharding |
| Neo4j | Causal clustering |
| Worker | More replicas + `WORKER_CONCURRENCY` |
| Beat | Single instance only |

## Health checks

| Service | Endpoint / check | Notes |
|---------|------------------|-------|
| `api` | `GET /healthz` (also `GET /readyz`) | `/readyz` currently returns a static `ok` (real dependency probes deferred) |
| `brain-api` | `GET /healthz` | On port 8020 |
| `ui` | Node HTTP GET on `:3000` | `node:22-alpine` ships no curl; the healthcheck uses node's `http` module |
| `postgres` | `pg_isready` | |
| `redis` | `redis-cli ping` | |
| `neo4j` | TCP connect to Bolt `:7687` | Lightweight `/dev/tcp` check — avoids cold-starting a cypher-shell JVM each interval |
| `qdrant` | none in-container | Image ships no HTTP client; dependants use `service_started` + their own retries |
| `worker` | `celery … inspect ping` | |

```bash
curl -sf http://api:8000/healthz
curl -sf http://brain-api:8020/healthz
```

The Site Admin console surfaces the Beat heartbeat (a stale/absent heartbeat means
beat is down).

## Backup and restore

### PostgreSQL

The Postgres volume mounts at `/var/lib/postgresql` (not the default
`/var/lib/postgresql/data`).

```bash
# Backup
pg_dump -h <host> -U "$POSTGRES_USER" -d "$POSTGRES_DB" | gzip > km2-$(date +%Y%m%d).sql.gz

# Restore
gunzip -c km2-YYYYMMDD.sql.gz | psql -h <host> -U "$POSTGRES_USER" -d "$POSTGRES_DB"
```

For production use WAL archiving (point-in-time recovery) or the managed provider's
snapshots. Restore the dump as the admin user so RLS-owned objects and roles restore
correctly.

### Object storage (MinIO / S3)

The bucket is the only copy of uploaded originals — mirror it:

```bash
mc alias set src http://<minio>:9000 "$STORAGE_ACCESS_KEY" "$STORAGE_SECRET_KEY"
mc mirror src/km-documents ./backup/km-documents      # or provider replication
```

### Qdrant and Neo4j

Qdrant and Neo4j are rebuildable from source documents (re-ingest) but can be
snapshotted directly:

```bash
# Qdrant snapshot
curl -X POST "http://qdrant:6333/collections/<collection>/snapshots"

# Neo4j dump
neo4j-admin database dump neo4j --to-path=/backups
```

A full restore of a KM2 org is: restore Postgres → restore the MinIO bucket →
re-ingest documents to rebuild Qdrant/Neo4j (or restore their snapshots).

## TLS and ingress

The compose stacks do not include a reverse proxy — terminate TLS at an external
proxy (nginx, a cloud load balancer, etc.) that routes `/` to `ui:3000` and `/api/`
to `api:8000`. Only the `ui` service publishes a port in prod; keep `api`,
`brain-api`, and the datastores on the internal `km2_network` and unexposed. Example
nginx location split:

```nginx
location /     { proxy_pass http://ui:3000;  proxy_set_header Host $host; }
location /api/ { proxy_pass http://api:8000/; proxy_set_header Host $host; }
```

Keep the proxy's public origin identical to `CLERK_ALLOWED_AZP`, `API_CORS_ORIGINS`,
and `PUBLIC_BASE_URL`. For encrypted backends, use `rediss://`, `bolt+s://`, and
`sslmode=verify-full` on the respective connection strings.

## Security checklist

- [ ] All secrets provided via env / secret manager (no secrets in files or images).
- [ ] `POSTGRES_PASSWORD`, `APP_DB_PASSWORD`, `NEO4J_PASSWORD` are strong (32+ chars).
- [ ] `ORG_ENCRYPTION_KEY` set to a unique random value (not the dev default).
- [ ] `API_SECRET_KEY`, `BRAIN_API_KEY`, `INTERNAL_API_KEY` set and distinct.
- [ ] `CLERK_ALLOWED_AZP` and `API_CORS_ORIGINS` restricted to the real UI origin.
- [ ] `API_E2E_TEST_MODE` unset (no `X-Test-User` bypass in production).
- [ ] `API_DOCS_ENABLED=false` if the public API docs should be hidden.
- [ ] App connects as `km_app` (non-superuser) so RLS is enforced.
- [ ] Migrations applied through `039_entity_access_control`.
- [ ] `WORKFLOW_WEBHOOK_ALLOWLIST` scoped to intended hosts (empty = disabled).
- [ ] TLS on all external traffic; datastores not publicly exposed.

## Troubleshooting

**Workflows / agent schedules never fire.** Beat is not running (or two beats are
double-firing). Confirm exactly one `celery-beat` and check the Site Admin heartbeat.

**`Invalid endpoint` on upload.** `STORAGE_ENDPOINT` host contains an underscore, or
the bucket does not exist. Use a hostname without underscores and create the bucket.

**Every Clerk login 403s.** Usually the JWT template omits/mis-spells
`email_verified`, or `CLERK_ALLOWED_AZP` does not match the UI origin byte-for-byte.

**API 500s stripped of CORS (`Network Error` in the browser).** An unhandled backend
error — check the API logs, not the browser. RLS/role misconfig (app connected as the
wrong role) is a common cause.

**Document ingestion failing.** Check the document status (`GET /api/documents/{id}`),
worker logs, brain-api logs, and that a valid LLM key resolves for the org.

**Celery not processing.** Check worker logs and Redis connectivity
(`redis-cli -u $REDIS_URL ping`).

## Known gaps / TODO

- **`docker-compose.prod.yml` ships no object-store or reverse-proxy service.** It is
  a template; production deployments must supply managed S3 (or a MinIO override) and
  an ingress proxy.
- **`/readyz` is a static `ok`** — it does not yet probe DB/Redis/brain-api
  connectivity (`REDARCH-12`). Use `/healthz` for liveness; add external dependency
  probes for readiness gating.

> Reviewed 2026-07-16 against Alembic migration 039. Source of truth is the code; if this doc disagrees with the code, the code wins.
