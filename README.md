# Red Arch Knowledge Management Platform v2

AI-powered enterprise knowledge management with RAG, vector search, knowledge graphs, and fine-grained RBAC.

## Quick Start

```bash
# 1. Copy and configure environment
cp .env.example .env
# Edit .env with your values

# 2. Start infrastructure
make dev-infra

# 3. Run migrations
make migrate

# 4. Start all services
make dev

# 5. Open the UI
open http://localhost:3000
```

## Architecture

| Service | Port | Description |
|---------|------|-------------|
| **api** | 8000 | FastAPI REST API (auth, RBAC, CRUD) |
| **brain-api** | 8020 | Knowledge brain (vector search, RAG, graph) |
| **worker** | — | Celery workers (document processing) |
| **ui** | 3000 | Next.js frontend |

**Infrastructure:** PostgreSQL 18 (with RLS), Qdrant, Neo4j, Redis

> Authentication is handled by an external **Keycloak** identity provider
> (OIDC). See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for configuration.

## Development

```bash
make help          # Show all commands
make lint          # Run linter
make type-check    # Run type checker
make test          # Run all tests
make test-cov      # Run tests with coverage
make format        # Auto-format code
```

## Documentation

Full documentation lives in [`docs/`](docs/):

| Document | Description |
|----------|-------------|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Service boundaries, data flow, deployment topology |
| [DATABASE.md](docs/DATABASE.md) | Schema, Row-Level Security policies, relationships |
| [RBAC.md](docs/RBAC.md) | 32-bit access-mask model and permission calculation |
| [API.md](docs/API.md) | REST endpoints with request/response examples |
| [DEPLOYMENT.md](docs/DEPLOYMENT.md) | Docker Compose, secrets, scaling, backup/restore |
| [DEVELOPMENT.md](docs/DEVELOPMENT.md) | Local setup, make commands, debugging |
| [REQUIREMENTS.md](docs/REQUIREMENTS.md) | Product requirements |
| [FEATURES.md](docs/FEATURES.md) | Feature overview |

Contributing guidelines: [CONTRIBUTING.md](CONTRIBUTING.md) ·
Changelog: [CHANGELOG.md](CHANGELOG.md) ·
License: [Apache-2.0](LICENSE)

## Project Structure

```
red-arch-km-2/
├── packages/           # Shared libraries
│   ├── access_mask/    # 32-bit permission encoding
│   ├── brain_sdk/      # Chunking, embedding, vector/graph stores
│   └── shared_config/  # Pydantic Settings
├── services/
│   ├── api/            # Main REST API
│   ├── brain_api/      # Knowledge brain + RAG
│   └── worker/         # Celery document processing
├── ui/                 # Next.js frontend
└── docker/             # Docker Compose + Dockerfiles
```
