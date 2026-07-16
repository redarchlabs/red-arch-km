# Development Guide

How to run, test, and debug KM2 (the Red Arch Knowledge Management Platform) on a
local machine. For engineers working on the Python API, brain service, Celery
worker, or the Next.js UI. The default local layout is a **hybrid host/Docker
stack**: infrastructure and the RAG/worker services run in Docker, while the API
and UI run on the host for fast iteration.

## Table of Contents

- [Prerequisites](#prerequisites)
- [Quick start](#quick-start)
- [Environment files: `.env` vs `.env.host`](#environment-files-env-vs-envhost)
- [The hybrid dev stack](#the-hybrid-dev-stack)
- [Service ports](#service-ports)
- [Make command reference](#make-command-reference)
- [Database migrations](#database-migrations)
- [Running services individually](#running-services-individually)
- [Testing](#testing)
- [Formatting, linting, type-checking](#formatting-linting-type-checking)
- [Debugging](#debugging)
- [The MCP dev tool](#the-mcp-dev-tool)
- [Go rewrite](#go-rewrite)
- [Known gaps / TODO](#known-gaps--todo)

## Prerequisites

| Tool | Version | Notes |
|------|---------|-------|
| Python | 3.12+ | `pyproject.toml` sets `requires-python = ">=3.12"`. |
| `uv` | latest | Package/workspace manager. Install below. |
| Node.js | 22+ | UI dev server + the MCP tool. UI image is `node:22-alpine`. |
| Docker | 24+ | With Compose v2 (`docker compose`, not `docker-compose`). |
| OpenAI API key | — | Embeddings, chat, OCR (per-org keys can override the central one). |
| `psql` / `lsof` | optional | `run-stack.sh` uses `lsof` to free ports; `psql` for direct DB access. |

### Installing uv

```bash
# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Or via pip
pip install uv
```

`uv` reads the workspace defined in the root `pyproject.toml` (`[tool.uv.workspace]`,
members `packages/*` + `services/*`) and creates a single root `.venv/`. The Go
modules (`packages/accessmask`, `packages/shared`, `services/*-go`) are excluded
from that workspace.

## Quick start

The one-command path uses the hybrid stack launcher `run-stack.sh` (see
[The hybrid dev stack](#the-hybrid-dev-stack)):

```bash
# 1. Clone and enter the repository
git clone https://github.com/redarchlabs/red-arch-km-2.git
cd red-arch-km-2

# 2. Environment files (containers read .env; host processes read .env.host)
cp .env.example .env          # fill in the REQUIRED values (see below)
cp .env .env.host             # then edit URLs to localhost (see below)

# 3. Install Python deps (workspace members + dev tools) and build the venv
uv sync --all-packages --extra dev

# 4. Start the whole hybrid stack (infra + brain-api + worker + beat + host API + UI).
#    This also runs `make migrate` for you before starting the API.
./run-stack.sh

# 5. Open the UI
open http://localhost:3000
```

On first run, check `/tmp/km2_api_dev.log` for the setup-token banner, then open
`http://localhost:3000/setup`.

The fully-dockerized alternative (`make dev`) also works and is described under
[Make command reference](#make-command-reference); it differs from the hybrid
stack in that the API/UI run in containers with `uvicorn --reload`.

## Environment files: `.env` vs `.env.host`

Two env files exist because the stack is split between Docker and the host:

- **`.env`** — read by `make` targets and Docker Compose. Service URLs use Docker
  service names (`postgres:5432`, `redis:6379`, `qdrant:6333`, `neo4j:7687`,
  `minio:9000`, `brain-api:8020`). Copy from `.env.example`.
- **`.env.host`** — read by the host processes (`uvicorn`, `next dev`) that
  `run-stack.sh` launches with `--env-file .env.host`. Same secrets, but URLs
  point at `localhost` and the published ports (`localhost:5433` for Postgres,
  `localhost:9000` for MinIO, etc.). `run-stack.sh` refuses to start without it.

Keep secrets in sync between the two (especially `OPENAI_API_KEY`, `BRAIN_API_KEY`,
`API_SECRET_KEY`, `POSTGRES_PASSWORD`). Required values called out in
`.env.example`:

| Var | Purpose |
|-----|---------|
| `POSTGRES_PASSWORD` | Postgres superuser password. |
| `NEO4J_PASSWORD` | Neo4j auth. |
| `STORAGE_SECRET_KEY` | MinIO root password (object storage). |
| `OPENAI_API_KEY` | Central fallback for embeddings/chat/OCR. |
| `API_SECRET_KEY` | JWT signing secret. Required (`Settings.secret_key`, `services/api/src/api/config.py`), no default — the API and the full test suite fail to boot without it. |
| `BRAIN_API_KEY` | Service-to-service auth for the brain API. |
| `INTERNAL_API_KEY` | Worker → API callback auth. |
| `CLERK_JWT_ISSUER`, `CLERK_ALLOWED_AZP`, `CLERK_SECRET_KEY` | Clerk backend verifier (see `.env.example` for the exact `azp`/issuer rules). |
| `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `NEXT_PUBLIC_CLERK_JWT_TEMPLATE` | Clerk UI. The JWT template is `redarch-km`. |

`API_DEBUG=true` enables the interactive API docs (`/docs`, `/redoc`) — they are
served only in debug mode (`api.main`). See [RBAC.md](RBAC.md) for how the Clerk
token maps to the Postgres `app_user` RLS role.

## The hybrid dev stack

`./run-stack.sh` orchestrates the whole hybrid layout:

```
docker : postgres(5433→5432) redis qdrant neo4j minio mailpit
         brain-api(8020) | celery worker + beat
host   : FastAPI api via uvicorn (8000, reads .env.host)
         Next.js UI dev server (3000)
```

Modes:

| Command | Effect |
|---------|--------|
| `./run-stack.sh` (or `restart`) | Start everything. Always kills any host API/UI on `:8000`/`:3000` first so a stale process can't shadow the fresh one; infra + `make migrate` run idempotently. |
| `./run-stack.sh stop` | Stop the host API/UI and the `km2_brain_api` / `km2_worker_fixed` / `km2_beat` containers. Infra containers keep running (`make down` stops those). |
| `./run-stack.sh --rebuild` | Rebuild the brain-api + worker images from source and recreate their containers, then start. Combine with a mode, e.g. `restart --rebuild`. Use after changing worker/brain-api code. |

What the script does, in order: brings up infra (re-asserting the
`redis`/`qdrant`/`postgres`/`neo4j`/`minio`/`mailpit` network aliases on
`km2_network`), waits for Postgres, runs `make migrate`, starts `brain-api`, the
worker (`km2_worker_fixed`), **and beat (`km2_beat`)**, then launches the host
`uvicorn` API and `next dev` UI, and polls health.

The worker and beat run as bespoke `docker run` containers (not the plain Compose
services) because the hybrid layout needs the worker to reach the **host** API via
the Docker gateway while reaching the **dockerized** brain-api by service name.
Beat is a separate single-process container so the periodic tasks (the workflow
outbox sweep every 10s, partition maintenance) fire exactly once regardless of
worker concurrency — see [Debugging](#debugging) for why beat matters.

## Service ports

| Service | Port | URL / notes |
|---------|------|-------------|
| UI (Next.js) | 3000 | http://localhost:3000 — host `next dev` (hybrid) or `km2_ui` container (`make dev`). |
| API (FastAPI) | 8000 | http://localhost:8000 — host `uvicorn`, `.env.host`. Health: `/healthz`, `/readyz`. |
| API interactive docs | 8000 | `/docs`, `/redoc` (only when `API_DEBUG=true`); `/api/v1/docs` gated by `API_DOCS_ENABLED`. |
| Brain API | 8020 | http://localhost:8020 — `km2_brain_api`. Health: `/healthz`. |
| PostgreSQL | 5433 | localhost:5433 → 5432 in-container. Published on 5433 to avoid clashing with other local Postgres. |
| Redis | 6379 | Celery broker/result + rate limits. |
| Qdrant | 6333 | http://localhost:6333 (vectors). |
| Neo4j Browser / Bolt | 7474 / 7687 | http://localhost:7474 · bolt://localhost:7687 (knowledge graph). |
| MinIO S3 / console | 9000 / 9001 | Object storage; console at http://localhost:9001. Bucket `km-documents` auto-created. |
| Mailpit SMTP / web | 1025 / 8025 | Dev mail catcher; open http://localhost:8025 to see captured intake-form emails. |
| Flower | 5555 | http://localhost:5555 (Celery monitor) — part of the full `make dev` stack. |
| pgAdmin | 81 | http://localhost:81 — `make dev` `dev-tools` Compose profile only. |

## Make command reference

`make help` prints the list. Every target sources `.env` (the Makefile begins with
`include .env`).

| Target | What it does |
|--------|--------------|
| `make dev-infra` | Start infra only (Postgres, Redis, Qdrant, Neo4j, MinIO, Mailpit) via `docker-compose.infra.yml`. |
| `make dev` | Start the **fully-dockerized** dev stack (`docker-compose.dev.yml`): source bind-mounts + `uvicorn --reload` for api/brain-api. |
| `make down` | Stop the app + infra containers. |
| `make logs` | Tail Compose logs for all services. |
| `make lint` | `ruff check .` |
| `make format` | `ruff format .` then `ruff check --fix .` |
| `make type-check` | `mypy packages/ services/` (strict). |
| `make test` | `pytest -x --tb=short` (all tests — needs `API_SECRET_KEY`; see [Testing](#testing)). |
| `make test-unit` | `pytest -x --tb=short -m unit` |
| `make test-integration` | `pytest -x --tb=short -m integration` (needs infra up). |
| `make test-cov` | Coverage report, `--cov-fail-under=80`. |
| `make migrate` | Alembic `upgrade head` against `localhost:5433` as the admin `POSTGRES_USER`. |
| `make migrate-create MSG="…"` | Autogenerate a new Alembic revision. |
| `make seed-e2e` | Seed org/roles/`e2e_admin` for the E2E suite (idempotent). Prints `SEEDED_ORG_ID`. |
| `make install-hooks` | `pre-commit install`. |
| `make clean` | Remove `__pycache__`, `.pytest_cache`, `.mypy_cache`, `*.egg-info`. |

**Go rewrite targets** (in-progress; see [Go rewrite](#go-rewrite)): `dev-go`,
`go-build`, `go-test`, `go-test-cover`, `go-lint`, `go-run-api`,
`go-run-brain-api`, `go-run-worker`, `go-mod-tidy`, `go-clean`, `go-migrate`,
`go-migrate-down`, `go-migrate-create`, `go-sqlc`.

## Database migrations

Migrations are Alembic revisions in `services/api/alembic/versions/`, currently
through **039_entity_access_control**.

```bash
make migrate                          # alembic upgrade head (localhost:5433, admin role)
make migrate-create MSG="add x table" # autogenerate a revision from model changes
```

`run-stack.sh` runs `make migrate` for you on every start, so a freshly pulled
migration is applied before the API reads the schema (otherwise the API 500s with
`relation … does not exist`). Migrations run as the admin `POSTGRES_USER` — the app
itself connects as the non-superuser `km_app` role so Postgres RLS is enforced
(`docker/init-db.sql`, migration 035). See [DATABASE.md](DATABASE.md) for the
schema, RLS model, and migration history.

Inspect state directly:

```bash
cd services/api && DATABASE_URL=postgresql+asyncpg://redarch:$POSTGRES_PASSWORD@localhost:5433/redarch_km \
  uv run alembic current    # or: alembic history
```

## Running services individually

`run-stack.sh` is the normal path, but each process can be started on its own. The
root `.venv` holds all Python entry points.

### API (host, matches run-stack)

```bash
# No --reload in the hybrid layout — restart after Python edits (see Debugging).
.venv/bin/uvicorn api.main:app --env-file .env.host \
  --host 0.0.0.0 --port 8000 --app-dir services/api/src
```

### Brain API

```bash
cd services/brain_api
uv run uvicorn brain_api.main:app --port 8020
```

### Celery worker + beat

```bash
cd services/worker
uv run celery -A worker.celery_app worker --loglevel=info    # executes tasks
uv run celery -A worker.celery_app beat   --loglevel=info    # schedules periodic tasks
```

Both the worker **and** beat are required for anything that depends on the workflow
outbox (see [Debugging](#debugging)).

### UI

```bash
cd ui
npm install
npm run dev            # http://localhost:3000
```

The UI uses **npm** (`ui/package-lock.json`). Scripts: `dev`, `build`, `lint`
(`eslint src/`), `type-check` (`tsc --noEmit`), `test` (`vitest run`), `test:e2e`
(`playwright test`).

## Testing

Python tests live under each package/service (`services/api/tests/{unit,integration}`,
`packages/*/tests`, etc.). Config is in the root `[tool.pytest.ini_options]`:
`asyncio_mode = auto`, markers `unit` / `integration`, and
`addopts = "--import-mode=importlib"`.

> **`--import-mode=importlib` is mandatory and already set in `addopts`.** The
> default `prepend` mode collapses both `services/api/tests/integration` and
> `services/brain_api/tests/integration` to the bare package `integration` and
> aborts collection with `ImportPathMismatchError`. importlib mode also requires
> that test dirs carry **no** `__init__.py`. If you invoke pytest from a subdir
> whose config doesn't inherit this, pass `--import-mode=importlib` explicitly.

> **`API_SECRET_KEY` must be set for a full run.** `Settings.secret_key` is
> required with no default, so importing the app (which most suites do) fails
> without it. Some modules `os.environ.setdefault("API_SECRET_KEY", "test-secret")`
> themselves, but a clean root-level run needs it exported.

```bash
# Unit tests (no external services)
API_SECRET_KEY=test-secret uv run python -m pytest -m unit

# All tests (unit + integration) — integration needs infra up first
make dev-infra
API_SECRET_KEY=test-secret uv run python -m pytest
# or:  API_SECRET_KEY=test-secret make test

# Coverage (80% gate)
API_SECRET_KEY=test-secret make test-cov

# A single file / function
API_SECRET_KEY=test-secret uv run pytest services/api/tests/unit/test_folder_service.py -v
API_SECRET_KEY=test-secret uv run pytest \
  services/api/tests/unit/test_folder_service.py::test_move_folder -v
```

- **Unit** (`-m unit`) — no DB/Redis; fast.
- **Integration** (`-m integration`) — need a running Postgres + Redis (`make
  dev-infra`). Migration/reporting/agent suites also set `API_SECRET_KEY`
  themselves via `setdefault`.
- **Workflow/agent tests that assert on outbox side effects** need Celery **beat**
  running so the outbox sweep fires (see [Debugging](#debugging)).

### End-to-end (Playwright, UI)

The seeded auth/RBAC E2E suite lives in `ui/tests/` and is documented in full in
[testing/e2e-seeded-auth.md](testing/e2e-seeded-auth.md) (the local recipe) and
[testing/e2e-ci-strategy.md](testing/e2e-ci-strategy.md) (CI gating). In short:

- The auth/RBAC specs authenticate to the Python API with a **header bypass**
  (`X-Test-User` / `X-Test-Secret`), enabled by `API_E2E_TEST_MODE=true` +
  `API_E2E_TEST_SECRET` (`services/api/src/api/auth/dependencies.py`). No Clerk
  users are provisioned for these.
- Recipe: `make dev-infra` → `make migrate` → start the API → `make seed-e2e` →
  `E2E_WITH_BACKEND=1 E2E_TEST_SECRET=<same as API_E2E_TEST_SECRET> npx playwright test`
  from `ui/`. Without `E2E_WITH_BACKEND=1` the heavy specs `test.skip()` and only
  `smoke.spec.ts` runs.
- The seeded run is **manual + nightly** in CI, not per-PR (secret-exposure and
  cost — see the CI-strategy doc).

## Formatting, linting, type-checking

### Python (ruff + mypy)

Config lives in the root `pyproject.toml`: ruff `line-length = 120`,
`target-version = "py312"`, lint rule sets `E/W/F/I/B/C4/UP/S/T20/SIM` (bandit `S`
included; `S101` allowed in tests). mypy is `strict = true`, excluding tests,
`alembic/env.py`, and `scripts/seed_e2e.py`.

```bash
make lint          # ruff check .
make format        # ruff format . && ruff check --fix .
make type-check    # mypy packages/ services/
```

### TypeScript (ESLint)

```bash
cd ui
npm run lint         # eslint src/
npm run type-check   # tsc --noEmit
```

### Pre-commit hooks

```bash
make install-hooks   # pre-commit install
```

## Debugging

### The API does NOT hot-reload in the hybrid stack

`run-stack.sh` launches `uvicorn` **without** `--reload`, so a Python change to the
API is not picked up until you restart:

```bash
./run-stack.sh restart    # relaunches the host API + UI
```

(The `next dev` UI process *does* hot-reload. The `make dev` fully-dockerized stack
runs `uvicorn --reload` — that layout reloads on host edits via bind-mounts.)

### Log locations

| Process | Where |
|---------|-------|
| Host API | `/tmp/km2_api_dev.log` |
| Host UI | `/tmp/km2_ui_dev.log` |
| Brain API | `docker logs km2_brain_api` |
| Worker | `docker logs km2_worker_fixed` |
| Beat | `docker logs km2_beat` |

### "Network Error" in the UI usually means a backend 500

An axios **"Network Error"** in the browser is typically **not** a connectivity
problem — it's a backend 500 whose response lost its CORS headers, so the browser
reports it as a network failure. The real stack trace is in `/tmp/km2_api_dev.log`.
Read that log before chasing CORS/proxy config.

### No workflows/agents firing? Beat must be running

The automation engine is **poll-based**: Celery **beat** enqueues the workflow
outbox sweep (every 10s); the worker only *executes* enqueued tasks. Without beat,
`sweep_outbox` is never scheduled — create/update events pile up as `pending` in
the outbox and no workflow ever runs. `run-stack.sh` starts `km2_beat`; verify it
with `docker ps | grep km2_beat`. Trace a stuck automation through
outbox → `workflow_runs` → `run_steps`. See [WORKFLOW_ENGINE.md](WORKFLOW_ENGINE.md).

### Inspecting the datastores

```bash
# Postgres (host port 5433)
psql -h localhost -p 5433 -U redarch -d redarch_km

# Redis
docker exec -it km2_redis redis-cli ping

# Qdrant collections
curl http://localhost:6333/collections

# Worker task inspection
docker exec km2_worker_fixed celery -A worker.celery_app inspect active
```

## The MCP dev tool

`tools/km2-mcp` is an MCP server that lets an agent (Claude Code, etc.) drive the
running KM2 API on your behalf. It is registered for this repo via the root
`.mcp.json` (`node tools/km2-mcp/dist/index.js`, with `KM2_APP_URL`,
`KM2_API_URL`, `KM2_BROWSER_CHANNEL`).

Auth: the server owns a persistent Playwright Chromium profile pointed at the
running web app and harvests a fresh Clerk token per call via
`window.Clerk.session.getToken()` — it stores **no** secrets and rides your live
session with your permissions (RLS/org-scoping still enforced server-side).

Build it once (requires the KM2 stack running on `:3000` / `:8000`):

```bash
cd tools/km2-mcp
npm install
npx playwright install chromium   # one-time
npm run build                     # emits dist/
npm test                          # optional unit tests (no browser/network)
```

After rebuilding, reconnect the MCP server in your agent (`/mcp`) so the new tools
register. Full tool catalogue and integration details are in
[MCP_AND_INTEGRATIONS.md](MCP_AND_INTEGRATIONS.md).

## Go rewrite

`services/api-go`, `services/brain-api-go`, `services/worker-go`, and
`packages/{accessmask,shared}` are an **in-progress Go rewrite** of the three
Python services. The Python services are the live/authoritative stack today. The Go
stack has its own Make targets (`make dev-go`, `go-*`), its own migrations
(`golang-migrate`, `services/api-go/migrations/`), and `sqlc` codegen — see the Go
targets in the Makefile and ARCHITECTURE.md §"Go Migration Status". Use the Python
path unless you are specifically working on the Go rewrite.

## Known gaps / TODO

- **Containerized UI + Clerk keys.** The `ui` service in `docker/docker-compose.yml`
  does not pass Clerk `NEXT_PUBLIC_*` build args, and Next bakes them at build time.
  The local hybrid stack (host `next dev` reading `.env.host` / `ui/.env.local`)
  sidesteps this; the container image wiring is a separate compose/CI task
  (see [testing/e2e-seeded-auth.md](testing/e2e-seeded-auth.md)).
- **E2E scope.** Only `smoke` / `auth` / `rbac` specs are CI-green; the
  brain/documents/chat specs need the brain service and real browser Clerk sign-in
  (tracked in [testing/e2e-ci-strategy.md](testing/e2e-ci-strategy.md)).

## Related docs

[ARCHITECTURE.md](ARCHITECTURE.md) · [DATABASE.md](DATABASE.md) ·
[DEPLOYMENT.md](DEPLOYMENT.md) · [RBAC.md](RBAC.md) · [API.md](API.md) ·
[WORKFLOW_ENGINE.md](WORKFLOW_ENGINE.md) ·
[MCP_AND_INTEGRATIONS.md](MCP_AND_INTEGRATIONS.md) ·
[testing/e2e-seeded-auth.md](testing/e2e-seeded-auth.md) ·
[testing/e2e-ci-strategy.md](testing/e2e-ci-strategy.md) · [README](../README.md)

> Reviewed 2026-07-16 against Alembic migration 039. Source of truth is the code; if this doc disagrees with the code, the code wins.
