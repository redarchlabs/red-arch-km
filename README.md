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

**Infrastructure:** PostgreSQL 16 (with RLS), Qdrant, Neo4j, Redis

## Development

```bash
make help          # Show all commands
make lint          # Run linter
make type-check    # Run type checker
make test          # Run all tests
make test-cov      # Run tests with coverage
make format        # Auto-format code
```

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
