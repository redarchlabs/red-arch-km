# Architecture

Red Arch Knowledge Management Platform v2 is a multi-tenant, AI-powered enterprise knowledge management system combining RAG (Retrieval-Augmented Generation), vector search, knowledge graphs, and fine-grained RBAC.

## System Overview

```
                           ┌─────────────┐
                           │   Browser   │
                           └──────┬──────┘
                                  │
                           ┌──────▼──────┐
                           │  Next.js UI │
                           │   (3000)    │
                           └──────┬──────┘
                                  │
              ┌───────────────────┼───────────────────┐
              │                   │                   │
       ┌──────▼──────┐     ┌──────▼──────┐     ┌──────▼──────┐
       │    Clerk    │     │   FastAPI   │     │  Brain API  │
       │  (External) │     │   (8000)    │     │   (8020)    │
       └─────────────┘     └──────┬──────┘     └──────┬──────┘
                                  │                   │
              ┌───────────────────┼───────────────────┤
              │                   │                   │
       ┌──────▼──────┐     ┌──────▼──────┐     ┌──────▼──────┐
       │ PostgreSQL  │     │   Qdrant    │     │    Neo4j    │
       │   (5432)    │     │   (6333)    │     │   (7687)    │
       └─────────────┘     └─────────────┘     └─────────────┘
              │
       ┌──────▼──────┐
       │    Redis    │
       │   (6379)    │
       └──────┬──────┘
              │
       ┌──────▼──────┐
       │   Celery    │
       │   Workers   │
       └─────────────┘
```

## Services

### API Service (services/api)

**Port:** 8000  
**Framework:** FastAPI with async SQLAlchemy

The main REST API handles:
- Authentication and authorization (Clerk session JWT)
- Multi-tenant CRUD operations (orgs, users, documents, folders)
- Row-Level Security (RLS) enforcement via PostgreSQL
- Document ingestion dispatch to Celery workers
- RBAC permission evaluation using 32-bit access masks

**Key Components:**
- `routers/` — HTTP endpoints (orgs, documents, folders, users, chat, search)
- `models/` — SQLAlchemy ORM models with RLS integration
- `repositories/` — Data access layer with tenant isolation
- `services/` — Business logic (permissions, user provisioning)
- `auth/` — Clerk session JWT validation and user context

### Brain API (services/brain_api)

**Port:** 8020  
**Framework:** FastAPI

The knowledge brain handles AI/ML operations:
- Document ingestion (chunking, embedding, summarization)
- Vector search via Qdrant
- Knowledge graph operations via Neo4j
- RAG query pipeline with streaming support
- Tenant isolation in vector and graph stores

**Key Components:**
- `routers/` — search, rag, ingest, health endpoints
- `services/` — IngestService, SearchService
- `stores.py` — Qdrant and Neo4j client wrappers

### Worker (services/worker)

**Framework:** Celery with Redis broker

Background task processing:
- Document ingestion orchestration
- Async metadata updates
- Scheduled maintenance tasks

### UI (ui/)

**Port:** 3000  
**Framework:** Next.js 14 with TypeScript

React-based single-page application:
- Server-side rendering for initial load
- Client-side navigation with React Query
- Clerk authentication flow
- Real-time chat with streaming responses

## Shared Packages

### packages/access_mask

32-bit RBAC permission encoding:
- Encodes org, region, department, role, and group into a single integer
- Efficient permission matching for folder/document access
- Used by both API and brain-api for access control

### packages/brain_sdk

AI/ML primitives:
- Document chunking strategies
- Embedding providers (OpenAI)
- Vector and graph store abstractions

### packages/shared_config

Configuration and observability:
- Pydantic Settings for all services
- Structured logging with correlation IDs
- OpenTelemetry tracing integration
- Redis and database connection helpers

## Data Flow

### Document Ingestion

1. User uploads document via UI
2. API validates request, creates document record in PostgreSQL
3. API dispatches Celery task with document metadata
4. Worker fetches document, sends to brain-api `/ingest-document`
5. Brain-api chunks text, generates embeddings via OpenAI
6. Chunks stored in Qdrant with access keys for filtering
7. If knowledge graph enabled, entities extracted and stored in Neo4j
8. Worker reports completion status back to API
9. Document status updated to COMPLETE

### RAG Query

1. User submits question via chat UI
2. API validates user permissions, calculates access mask
3. API proxies to brain-api `/ask` or `/ask/stream`
4. Brain-api embeds query, searches Qdrant with access key filter
5. Retrieved chunks passed to Neo4j for graph context (optional)
6. Combined context sent to OpenAI for answer generation
7. Response streamed back to UI via SSE

## Multi-Tenancy

### PostgreSQL Row-Level Security

Every tenant-scoped table has RLS policies enforced via `app.current_tenant_id`:

```sql
CREATE POLICY tenant_isolation_select ON documents
FOR SELECT
USING (org_id = current_setting('app.current_tenant_id', true)::uuid);
```

The API sets this context variable per-request based on the authenticated user's org membership.

### Vector Store Isolation

Qdrant collections are namespaced per tenant:
- `{tenant_id}_chunks` — Document chunks with embeddings
- `{tenant_id}_documents` — Document-level summaries

Access keys stored in point payloads enable folder-level filtering within a tenant.

### Graph Store Isolation

Neo4j nodes are labeled with tenant ID:
- Entity nodes carry `tenant_id` property
- Queries filter by tenant label

## Infrastructure

| Component | Image | Purpose |
|-----------|-------|---------|
| PostgreSQL 18 | postgres:18 | Primary data store with RLS |
| Redis 7.4 | redis:7-alpine | Celery broker, caching |
| Qdrant | qdrant/qdrant:v1.12.4 | Vector database |
| Neo4j 5.25 | neo4j:5.25.1 | Knowledge graph |
| Clerk | External SaaS | Identity provider |

## Observability

All services emit:
- Structured JSON logs with correlation IDs
- OpenTelemetry traces to configured collector
- Health endpoints (`/healthz`) for orchestration

Flower (port 5555) provides Celery task monitoring.  
PgAdmin (port 81) available in dev profile for database inspection.
