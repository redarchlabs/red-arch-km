# Red Arch Knowledge Management Platform

**KM2** is an open-source, multi-tenant platform for building AI-powered knowledge and
business applications. It combines an enterprise document brain — retrieval-augmented
generation (RAG) with passage-level citations, vector search, and a knowledge graph — with
a no-code application layer: custom entities, forms, views, dashboards, reports, and a
BPMN-style workflow engine. A governed org of AI agents can operate inside it, and
everything is protected by fine-grained, row-level-secured RBAC down to individual fields.

What that means in practice:

- **Knowledge you can trust** — ingest documents into a per-org brain; chat answers cite
  the exact passage they came from, and search results are filtered by the caller's
  permissions before they're ever returned.
- **Apps without code** — model entities and relationships, compose forms and dashboard
  views from a shared element-tree renderer, automate with workflows (email, HTTP,
  LLM grading, knowledge search, scheduling, webhooks), and report on any of it. The
  bundled LMS, HRMS, and ticketing designs are just configurations of these primitives.
- **AI that's governed** — an org chart of AI agents (with model tiering, an authority
  engine, approvals, and schedules) plus an in-app assistant that can author entities,
  forms, views, and workflows on request.
- **Enterprise controls** — Clerk (OIDC) authentication, org API keys with scopes, a
  versioned `/api/v1` API, HMAC-signed webhooks, release-based change management with
  diff/preview/rollback for promoting configuration between environments, and a
  cross-org site-admin console.
- **Isolation by default** — every org is isolated by PostgreSQL Row-Level Security plus
  tenant-bound repositories; per-entity write policies and per-field read policies
  (`server_only`, `workflow_only`) make records like quiz answer keys and certificates
  tamper-proof.

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
| **api** | 8000 | FastAPI REST API (auth, RBAC, entities, forms/views, workflows, agents, change management, `/api/v1`) |
| **brain-api** | 8020 | Knowledge brain (ingest, vector search, RAG chat, knowledge graph) |
| **worker** | — | Celery workers + beat (document processing, workflow outbox, agent schedules) |
| **ui** | 3000 | Next.js frontend |

**Infrastructure:** PostgreSQL 18 (with RLS), Qdrant, Neo4j, Redis, MinIO

> Authentication is handled by **Clerk**, a cloud OIDC identity provider. Machine access
> uses scoped org API keys. See [docs/AUTHENTICATION.md](docs/AUTHENTICATION.md).

An incremental **Go rewrite** of the three Python services lives in `services/*-go`
(see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)); the Python services are authoritative.

## Development

```bash
make help          # Show all commands
make lint          # Run linter
make type-check    # Run type checker
make test          # Run all tests
make test-cov      # Run tests with coverage
make format        # Auto-format code
```

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for the full local-stack guide.

## Documentation

Full documentation lives in [`docs/`](docs/). Start with
[FEATURES.md](docs/FEATURES.md) — it maps every capability to its deep doc.

### Platform

| Document | Description |
|----------|-------------|
| [FEATURES.md](docs/FEATURES.md) | Feature overview — the map to everything below |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Service boundaries, data flows, tenancy model, Go migration status |
| [DATABASE.md](docs/DATABASE.md) | Schema (through migration 039), RLS policies, indexes, backup |
| [API.md](docs/API.md) | REST endpoint reference — first-party API, Brain API, and versioned `/api/v1` |
| [AUTHENTICATION.md](docs/AUTHENTICATION.md) | Clerk OIDC flow, session → RLS, API keys & scopes, webhook signing |
| [RBAC.md](docs/RBAC.md) | 32-bit access masks, entity/field access control, admin roles |
| [KNOWLEDGE_ENGINE.md](docs/KNOWLEDGE_ENGINE.md) | Ingest pipeline, fact store, RAG retrieval, passage-level citations |
| [FORMS_AND_VIEWS.md](docs/FORMS_AND_VIEWS.md) | Element-tree forms/views platform, record lists, dashboards |
| [WORKFLOW_ENGINE.md](docs/WORKFLOW_ENGINE.md) | BPMN token engine, triggers, action catalog, connections |
| [AGENT_ORG.md](docs/AGENT_ORG.md) | Multi-tenant AI agent org: governance, runtime, model tiers, provisioner |
| [CHANGE_MANAGEMENT.md](docs/CHANGE_MANAGEMENT.md) | Releases, promotion between environments, diff/rollback, import/export |
| [MCP_AND_INTEGRATIONS.md](docs/MCP_AND_INTEGRATIONS.md) | MCP servers, inbound webhooks, outbound connections |
| [SITE_ADMIN.md](docs/SITE_ADMIN.md) | Cross-org instance administration console |
| [LMS.md](docs/LMS.md) | How the LMS reference app is built from platform primitives |

### Build guides (cookbooks)

Step-by-step recipes for building complete applications on the platform — see the
[guides index](docs/guides/README.md):

| Guide | Build |
|-------|-------|
| [BUILD_TICKETING.md](docs/guides/BUILD_TICKETING.md) | Support / help-desk ticketing: queues, SLAs, escalation, dashboards |
| [BUILD_HRMS.md](docs/guides/BUILD_HRMS.md) | HR management: onboarding/offboarding automation, review cycles |
| [BUILD_LMS.md](docs/guides/BUILD_LMS.md) | Learning management: courses, graded quizzes, LLM scenarios, certificates |

### Operations & process

| Document | Description |
|----------|-------------|
| [DEPLOYMENT.md](docs/DEPLOYMENT.md) | Docker Compose, environment reference, scaling, backup/restore |
| [DEVELOPMENT.md](docs/DEVELOPMENT.md) | Local setup, make commands, testing, debugging |
| [REQUIREMENTS.md](docs/REQUIREMENTS.md) | Product requirements (functional + non-functional) |

Contributing guidelines: [CONTRIBUTING.md](CONTRIBUTING.md) ·
Changelog: [CHANGELOG.md](CHANGELOG.md) ·
License: [Apache-2.0](LICENSE)

## Project Structure

```
red-arch-km-2/
├── packages/           # Shared libraries
│   ├── access_mask/    # 32-bit permission encoding
│   ├── brain_sdk/      # Chunking, embedding, vector/graph stores, fact engine
│   └── shared_config/  # Pydantic Settings
├── services/
│   ├── api/            # Main REST API (FastAPI)
│   ├── brain_api/      # Knowledge brain + RAG
│   ├── worker/         # Celery document processing + beat
│   └── *-go/           # In-progress Go rewrite of the above
├── ui/                 # Next.js frontend
├── tools/km2-mcp/      # MCP server for driving KM2 during development
├── docs/               # Documentation (see index above)
└── docker/             # Docker Compose + Dockerfiles
```
