# Red Arch Knowledge Manager - Local Development Guide

## Prerequisites

- **Python 3.11+** with `uv` package manager
- **Node.js 22+** with npm
- **Docker 24+** and Docker Compose v2
- **PostgreSQL client** (optional, for direct DB access)
- **OpenAI API key** (for embeddings and chat)

### Installing uv

```bash
# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Or via pip
pip install uv
```

---

## Quick Start

> **Hybrid dev stack (one command):** `./run-stack.sh` starts everything —
> infra containers, brain-api, the celery worker, the host uvicorn API
> (`.env.host`), and the Next.js dev server. `./run-stack.sh restart`
> force-restarts the host processes; `./run-stack.sh stop` stops the app.
> Requires a `.env.host` (copy of `.env` with localhost URLs + Clerk issuer).
> The fully dockerized alternative below (`make dev`) still works.

```bash
# 1. Clone and enter repository
git clone https://github.com/redarchlabs/red-arch-km-2.git
cd red-arch-km-2

# 2. Copy environment template
cp .env.example .env
# Edit .env with your values (see Environment Setup below)

# 3. Start infrastructure (PostgreSQL, Redis, Qdrant, Neo4j)
make dev-infra

# 4. Install Python dependencies
uv sync --all-packages

# 5. Run database migrations
make migrate

# 6. Start all services
make dev

# 7. Open the UI
open http://localhost:3000
```

---

## Environment Setup

Edit `.env` with your development values:

```bash
# Required: Database passwords
POSTGRES_PASSWORD=devpassword123
NEO4J_PASSWORD=devpassword123

# Required: OpenAI key for embeddings
OPENAI_API_KEY=sk-...

# Required: Service authentication
API_SECRET_KEY=dev-secret-key-change-in-prod
BRAIN_API_KEY=dev-brain-key
INTERNAL_API_KEY=dev-internal-key

# Optional: first-run setup token validity (seconds, default 86400 = 24h)
# API_SETUP_TOKEN_TTL_SECONDS=86400

# Clerk (dev instance or use X-Test-User header for E2E bypass)
CLERK_SECRET_KEY=<dev-clerk-key>
CLERK_JWT_ISSUER=<dev-clerk-issuer>
CLERK_ALLOWED_AZP=http://localhost:3000
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=<dev-clerk-publishable-key>
NEXT_PUBLIC_CLERK_SIGN_IN_URL=/login
NEXT_PUBLIC_CLERK_SIGN_UP_URL=/sign-up
NEXT_PUBLIC_CLERK_JWT_TEMPLATE=redarch-km
```

---

## Development Commands

```bash
# Show all available commands
make help

# Infrastructure
make dev-infra     # Start infrastructure only
make dev           # Start full stack (infra + services)
make down          # Stop all containers
make logs          # Tail logs for all services

# Code Quality
make lint          # Run ruff linter
make format        # Auto-format code (ruff)
make type-check    # Run mypy type checker

# Testing
make test          # Run all tests
make test-unit     # Run unit tests only
make test-integration  # Run integration tests only
make test-cov      # Run tests with coverage report (80% threshold)

# Database
make migrate       # Apply Alembic migrations
make migrate-create MSG="add users table"  # Create new migration

# Cleanup
make clean         # Remove __pycache__, .pytest_cache, etc.
make install-hooks # Install pre-commit hooks
```

---

## Service Ports

| Service | Port | URL |
|---------|------|-----|
| UI | 3000 | http://localhost:3000 |
| API | 8000 | http://localhost:8000 |
| API Docs | 8000 | http://localhost:8000/docs |
| Brain API | 8020 | http://localhost:8020 |
| PostgreSQL | 5433 | localhost:5433 (host), 5432 (container) |
| Redis | 6379 | localhost:6379 |
| Qdrant | 6333 | http://localhost:6333 |
| Neo4j Browser | 7474 | http://localhost:7474 |
| Neo4j Bolt | 7687 | bolt://localhost:7687 |
| Flower (Celery) | 5555 | http://localhost:5555 |
| pgAdmin | 81 | http://localhost:81 (dev-tools profile) |

---

## Project Structure

```
red-arch-km-2/
├── packages/                   # Shared Python libraries
│   ├── access_mask/            # 32-bit permission encoding
│   ├── brain_sdk/              # Chunking, embedding, vector/graph stores
│   └── shared_config/          # Pydantic Settings, logging, telemetry
│
├── services/
│   ├── api/                    # Main REST API (FastAPI)
│   │   ├── alembic/            # Database migrations
│   │   ├── src/api/
│   │   │   ├── auth/           # JWT/OIDC authentication
│   │   │   ├── models/         # SQLAlchemy models
│   │   │   ├── repositories/   # Data access layer
│   │   │   ├── routers/        # API endpoints
│   │   │   ├── schemas/        # Pydantic request/response models
│   │   │   ├── services/       # Business logic
│   │   │   └── tasks/          # Celery task definitions
│   │   └── tests/
│   │
│   ├── brain_api/              # Knowledge brain + RAG (FastAPI)
│   │   ├── src/brain_api/
│   │   │   ├── routers/        # Ingest, search, RAG endpoints
│   │   │   └── services/       # Ingestion, search orchestration
│   │   └── tests/
│   │
│   └── worker/                 # Celery background workers
│       ├── src/worker/
│       │   └── tasks/          # Ingest, metadata update tasks
│       └── tests/
│
├── ui/                         # Next.js frontend
│   ├── src/
│   │   ├── app/                # App router pages
│   │   ├── components/         # React components
│   │   └── lib/                # Utilities, API clients
│   └── tests/
│
├── docker/                     # Docker Compose + Dockerfiles
│   ├── docker-compose.yml      # Full stack
│   ├── docker-compose.infra.yml # Infrastructure only
│   ├── docker-compose.dev.yml  # Dev with hot-reload
│   └── docker-compose.prod.yml # Production
│
├── docs/                       # Documentation
│   ├── ARCHITECTURE.md
│   ├── DATABASE.md
│   ├── RBAC.md
│   ├── API.md
│   ├── DEPLOYMENT.md
│   └── DEVELOPMENT.md
│
├── tests/                      # Cross-service tests
│   └── load/                   # Load testing
│
├── Makefile                    # Development commands
├── pyproject.toml              # Root Python project config
└── uv.lock                     # Locked dependencies
```

---

## Running Services Individually

### API Service

```bash
cd services/api
uv run uvicorn api.main:app --reload --port 8000
```

### Brain API Service

```bash
cd services/brain_api
uv run uvicorn brain_api.main:app --reload --port 8020
```

### Celery Worker

```bash
cd services/worker
uv run celery -A worker.celery_app worker --loglevel=info
```

### Celery Beat (Scheduler)

```bash
cd services/worker
uv run celery -A worker.celery_app beat --loglevel=info
```

### UI (Next.js)

```bash
cd ui
npm install
npm run dev
```

---

## Testing

### Running Tests

```bash
# All tests
make test

# With coverage
make test-cov

# Specific test file
uv run pytest services/api/tests/unit/test_folder_service.py -v

# Specific test function
uv run pytest services/api/tests/unit/test_folder_service.py::test_move_folder -v

# Integration tests (requires running infra)
make dev-infra
make test-integration
```

### Test Structure

Tests are organized by service and type:

```
services/api/tests/
├── conftest.py              # Shared fixtures
├── unit/                    # Unit tests (no external deps)
│   ├── test_folder_service.py
│   └── test_permission_config.py
└── integration/             # Integration tests (need DB, Redis)
    ├── conftest.py
    ├── test_folder_move.py
    └── test_rls_isolation.py
```

### Test Database

Integration tests use environment variables for test DB:

```bash
DATABASE_URL=postgresql+asyncpg://test:test@localhost:5432/test_km
```

---

## Debugging

### Python Debugging (VS Code)

Add to `.vscode/launch.json`:

```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "API Service",
      "type": "debugpy",
      "request": "launch",
      "module": "uvicorn",
      "args": ["api.main:app", "--reload", "--port", "8000"],
      "cwd": "${workspaceFolder}/services/api",
      "env": {"DATABASE_URL": "..."}
    }
  ]
}
```

### Database Inspection

```bash
# Connect to PostgreSQL
docker compose -f docker/docker-compose.infra.yml exec postgres \
  psql -U redarch -d redarch_km

# Common queries
\dt                          # List tables
\d+ documents                # Describe table
SELECT * FROM orgs LIMIT 5;  # Query data
```

### Redis Inspection

```bash
docker compose -f docker/docker-compose.infra.yml exec redis redis-cli

# Commands
KEYS *                       # List all keys
GET celery-task-meta-*       # Get task result
LLEN celery                  # Queue length
```

### Qdrant Inspection

```bash
# List collections
curl http://localhost:6333/collections

# Get collection info
curl http://localhost:6333/collections/tenant_<org-uuid>

# Search (for debugging)
curl -X POST http://localhost:6333/collections/tenant_<org-uuid>/points/scroll \
  -H "Content-Type: application/json" \
  -d '{"limit": 10}'
```

### Log Levels

Set `LOG_LEVEL` in `.env`:

```bash
LOG_LEVEL=DEBUG  # Verbose
LOG_LEVEL=INFO   # Normal
LOG_LEVEL=WARNING  # Minimal
```

---

## Common Issues

### Database Connection Refused

```bash
# Check if PostgreSQL is running
docker compose -f docker/docker-compose.infra.yml ps postgres

# Check logs
docker compose -f docker/docker-compose.infra.yml logs postgres

# Verify port (5433 on host, 5432 in container)
psql -h localhost -p 5433 -U redarch -d redarch_km
```

### Migrations Fail

```bash
# Check current migration state
cd services/api && uv run alembic current

# Show migration history
cd services/api && uv run alembic history

# If stuck, verify RLS context is not interfering
# (migrations should run without RLS)
```

### Celery Tasks Not Running

```bash
# Check worker is connected
docker compose -f docker/docker-compose.yml logs worker

# Verify Redis connection
docker compose -f docker/docker-compose.infra.yml exec redis redis-cli ping

# Check task queue
docker compose -f docker/docker-compose.yml exec worker \
  celery -A worker.celery_app inspect active
```

### OpenAI API Errors

- Verify `OPENAI_API_KEY` is set correctly
- Check API quota at https://platform.openai.com/usage
- For rate limits, the system will retry automatically

### Type Errors After Changes

```bash
# Regenerate types after model changes
make type-check

# If mypy cache is stale
rm -rf .mypy_cache && make type-check
```

---

## Code Style

### Python (ruff)

```bash
# Check linting
make lint

# Auto-fix + format
make format
```

Configuration in `pyproject.toml`:
- Line length: 100
- Target: Python 3.11+
- Style: Black-compatible

### TypeScript/JavaScript (prettier, eslint)

```bash
cd ui
npm run lint
npm run format
```

---

## Pre-commit Hooks

Install hooks to run linting before commits:

```bash
make install-hooks
```

This runs:
- `ruff check` and `ruff format` on Python files
- Type checking via `mypy`

---

## IDE Setup

### VS Code Extensions

- Python (ms-python.python)
- Pylance (ms-python.vscode-pylance)
- Ruff (charliermarsh.ruff)
- SQLTools (mtxr.sqltools)
- Docker (ms-azuretools.vscode-docker)

### VS Code Settings

`.vscode/settings.json`:

```json
{
  "python.defaultInterpreterPath": ".venv/bin/python",
  "[python]": {
    "editor.defaultFormatter": "charliermarsh.ruff",
    "editor.formatOnSave": true
  },
  "python.analysis.typeCheckingMode": "basic"
}
```
