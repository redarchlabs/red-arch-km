# Architecture

Red Arch Knowledge Management Platform v2 is a multi-tenant, AI-powered enterprise
knowledge management system combining RAG (Retrieval-Augmented Generation), vector
search, a knowledge graph, and fine-grained RBAC.

> **Which stack ships?** The **Python** implementation (`services/api`,
> `services/brain_api`, `services/worker`, `ui/`) is authoritative and is what
> `run-stack.sh` and `docker-compose.prod.yml` run. A parallel **Go** rewrite
> (`services/api-go`, `services/brain-api-go`, `services/worker-go`) is an in-progress
> port wired only into `docker-compose.go.yml` — see [§9 Go Migration](#9-go-migration-status).
> This document describes the Python stack unless stated otherwise.

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Services](#2-services)
3. [Shared Packages](#3-shared-packages)
4. [Request & Tenancy Model](#4-request--tenancy-model)
5. [Data Flows](#5-data-flows)
6. [Multi-Tenancy & Isolation](#6-multi-tenancy--isolation)
7. [Security Boundaries](#7-security-boundaries)
8. [Infrastructure & Deployment](#8-infrastructure--deployment)
9. [Go Migration Status](#9-go-migration-status)
10. [Observability](#10-observability)

---

## 1. System Overview

```
                              ┌─────────────┐
                              │   Browser   │
                              └──────┬──────┘
                                     │ HTTPS
                              ┌──────▼──────┐        ┌─────────────┐
                              │  Next.js UI │◄──────►│    Clerk    │
                              │   (3000)    │  OIDC  │  (External) │
                              └──────┬──────┘        └─────────────┘
                    Bearer JWT + X-Org-ID │  (SSE for chat)
                                     │
                              ┌──────▼──────┐   X-API-Key   ┌─────────────┐
                              │  FastAPI    │──────────────►│  Brain API  │
                              │  API (8000) │◄──────────────│   (8020)    │
                              └──┬───────┬──┘   (SSE)       └──┬───────┬──┘
                     RLS session │       │ dispatch            │       │
                   ┌─────────────┘       │ (Celery)     ┌──────┘       └──────┐
            ┌──────▼──────┐       ┌──────▼──────┐ ┌─────▼─────┐         ┌──────▼──────┐
            │ PostgreSQL  │       │    Redis    │ │  Qdrant   │         │    Neo4j    │
            │  18 (5433)  │       │ 7.4 (6379)  │ │(6333) vec │         │(7687) graph │
            └─────────────┘       └──────┬──────┘ └───────────┘         └─────────────┘
                                         │ broker           ▲
                                  ┌──────▼──────┐           │ X-API-Key
                                  │   Celery    │───────────┘
                                  │   Worker    │──► MinIO/S3 (9000, originals)
                                  └─────────────┘
                     internal callback (X-Internal-API-Key) ──► API /api/internal/*
```

**One-line summary:** the UI holds no server session — it attaches a Clerk Bearer
token and an `X-Org-ID` header to every call. The API enforces RBAC + RLS, owns the
relational data, and delegates all AI/ML work to the Brain API and heavy processing
to Celery workers. The Brain API owns the vector store and knowledge graph.

---

## 2. Services

### 2.1 API Service — `services/api` (port 8000)

FastAPI + async SQLAlchemy. App factory `create_app` (`src/api/main.py`), title
"Red Arch Knowledge Management API". Responsibilities:

- Clerk session-JWT verification and user auto-provisioning.
- Multi-tenant CRUD (orgs, users, memberships, dimensions, folders, tags, attributes, documents, chat sessions).
- RLS enforcement (sets `app.current_tenant_id` per request) + explicit `org_id` repository filtering.
- RBAC permission evaluation using 32-bit access masks.
- Document ingestion dispatch to Celery; status callbacks via an internal router.
- RAG/search **proxy** to the Brain API (including SSE pass-through).
- First-run setup wizard and the site-admin console.

**Layout:** `routers/` (HTTP), `models/` (SQLAlchemy ORM + RLS), `repositories/`
(org-scoped data access), `services/` (business logic: permissions, provisioning,
folder tree, brain client, storage, setup token), `auth/` (Clerk + dependencies),
`middleware/` (request logging), `tasks/` (Celery dispatch signatures).

**Routers mounted** (`main.py`): `health` (`/`), `auth` (`/api/auth`), `orgs`,
`users`, `documents`, `folders`, `tags`, `chat`, `search`, `dimensions`,
`memberships`, `attributes`, `internal`, `setup`, `admin` (all under `/api/*`).
Full endpoint reference: [API.md](API.md).

### 2.2 Brain API — `services/brain_api` (port 8020)

FastAPI. All AI/ML operations; authenticated service-to-service via `X-API-Key`.
Blocking clients (Qdrant/Neo4j/OpenAI) run off the event loop via `asyncio.to_thread`.

- **Ingest** (`routers/ingest.py`, `/api/*`): chunk → embed + summarize → upsert
  vectors → document summary/tree → optional triplet extraction → Neo4j.
  Also `remove-document`, `update-document-metadata`, `init-tenant`,
  `remove-tenant`, paginated chunk/summary reads.
- **Search** (`routers/search.py`): `vector-search`, `vector-chat` (hybrid RAG).
- **RAG** (`routers/rag.py`, `/api/v1/*`): `ask` and `ask/stream` (SSE with
  `sources`/`graph`/`delta`/`done`/`error` events).
- `IngestService`: chunk size 500 / overlap 20 tokens; concurrent embed+summarize;
  triplet extraction with 8 workers. `SearchService`: strict "answer only from
  context, cite `[n]`" system prompt; history clamped to last 10 turns; graph
  context capped at 10 facts.

### 2.3 Worker — `services/worker` (Celery, Redis broker)

Background processing, `task_acks_late=True`, `prefetch_multiplier=1`,
soft/hard time limits 1740/1800 s. Tasks:

- `task_ingest_document` — text-only docs → POST to Brain.
- `task_extract_and_ingest` — uploaded files: fetch original from object storage →
  extract text (`.txt/.md` direct, `.docx` mammoth, `.doc` antiword, PDF/image
  Tesseract or OpenAI vision) → POST to Brain.
- `task_update_document_metadata` — re-propagate tags/access-keys/title to vectors.
- Shared `_ingest_common`: retry only on 5xx/429/network; best-effort status
  callback to the API's internal router (`X-Internal-API-Key`).

The worker image bundles `tesseract-ocr`, `poppler-utils`, and `antiword`.

### 2.4 UI — `ui/` (Next.js 15, port 3000)

App Router, React 18, TypeScript, Tailwind v4. Auth via `@clerk/nextjs`. **No React
Query** — data fetching is imperative via an axios singleton (`lib/api/client.ts`)
plus native `fetch` for SSE. State via React Context (`Auth`, `Org`, `Theme`, `Help`).
Route groups: `(auth)` public (`/login`, `/sign-up`), `(authenticated)` app shell
(`/chat`, `/documents`, `/documents/[id]`, `/documents/search`, `/folders`,
`/folders/[id]`, `/admin`, `/site-admin`), and `/setup`.

Key UI capabilities: streaming chat with a scope selector and citations; a
two-pane Explorer-style resource browser (virtualized via `react-window`);
document upload with OCR/AI extraction; a CodeMirror Markdown editor with a
table toolbar; a scroll-synced document reader; a context-sensitive help dock;
and Light/Dark/Red Arch themes. The axios interceptor attaches `Authorization`
and `X-Org-ID`, where a per-request `X-Org-ID` wins over the ambient org (used by
the cross-org site-admin console).

---

## 3. Shared Packages

| Package | Purpose |
|---------|---------|
| `packages/access_mask` | Pure-computation 32-bit RBAC mask: `encode`/`decode`/`matches`, layout `[org:11][region:5][role:5][group:7][dept:4]`. Used by the API to compile folder/document permissions and user entitlements |
| `packages/brain_sdk` | AI/ML primitives: sentence-aware chunker (`o200k_base`), OpenAI embedding provider, hierarchical `ChunkSummarizer`, `TripletExtractor`, and the Qdrant vector-store and Neo4j graph-store abstractions |
| `packages/shared_config` | Pydantic settings (DB/Redis/OpenAI/observability), JSON logging with OTel correlation, and OTLP telemetry setup |

Go counterparts exist under `packages/accessmask` and `packages/shared/{logging,telemetry}`.

---

## 4. Request & Tenancy Model

Every authenticated request carries a Clerk **Bearer JWT** and (for tenant-scoped
endpoints) an **`X-Org-ID`** header. There is no server-side session.

Two DB session dependencies (`src/api/dependencies.py`):

- **`get_tenant_db`** — for tenant-scoped endpoints. Inside the transaction:
  1. `SET LOCAL ROLE app_user` — drop from the privileged connection role to a
     `NOBYPASSRLS` role so `FORCE ROW LEVEL SECURITY` actually applies.
  2. `set_config('app.current_tenant_id', <org_id>, is_local => true)` — the GUC
     RLS policies compare against `org_id`.
  Both are transaction-local and auto-reset on commit/rollback → safe on a pooled
  connection.
- **`get_db`** — for cross-org / non-tenant endpoints (auth, `/users/me`, orgs,
  admin, setup). Stays on the privileged role and deliberately bypasses RLS so
  cross-org reads (e.g. the membership lookup in `require_org_access`) don't fail
  closed. Isolation still holds because repositories filter by `org_id` explicitly.

Auth dependency chain: `get_current_user` → `require_org_access` (→ `OrgContext`
with membership + dimensions; site admins get a synthetic org-admin membership) →
`require_org_admin` / `require_site_admin` / `require_internal_api_key`.

---

## 5. Data Flows

### 5.1 Document Ingestion

```
UI ──upload/create──► API ──persist row (PENDING)──► PostgreSQL
                       │
                       ├─ upload: stream original ──► MinIO/S3 ({org}/{key}/{file})
                       └─ dispatch Celery task ─────► Redis broker
                                                        │
                                              Worker ◄──┘
                                              ├─ (upload) fetch original, extract text
                                              │    (.docx→mammoth, .doc→antiword,
                                              │     pdf/img→Tesseract | OpenAI vision)
                                              ├─ POST /api/ingest-document ──► Brain API
                                              │        ├─ chunk (500/20) → embed → Qdrant
                                              │        ├─ hierarchical summaries + tree
                                              │        └─ (if enabled) triplets → Neo4j
                                              └─ POST status callback ──► API /api/internal/*
                                                                          └─ status=SUCCESS/FAILED
```

Re-ingest (content replace) purges existing vectors first, because Brain ingest is
not idempotent (fresh UUIDs per run).

### 5.2 RAG Query (streaming)

```
UI ──POST /api/search/chat/stream (fetch, SSE)──► API
     │  computes user access masks (org admin → unrestricted)
     │  maps selected folder_ids → folder:<id> tags (OR); free tags (AND)
     └─ proxy ──► Brain /api/v1/ask/stream
                   ├─ embed query → Qdrant search (access_keys + tags filter, top-5)
                   ├─ (optional) knowledge-graph fact lookup (≤10, RBAC-filtered)
                   ├─ dedupe to unique source documents
                   ├─ build context → OpenAI chat (gpt-5-mini, temp 0.3)
                   └─ stream events: sources → graph → delta… → done
     ◄─ SSE bytes forwarded verbatim ─┘
UI renders tokens incrementally, turns [n] into citation links, can AbortController-cancel
```

---

## 6. Multi-Tenancy & Isolation

**PostgreSQL RLS.** Eleven tenant tables (`regions, departments, roles, groups,
folders, tags, documents, document_access, document_attribute_definitions,
chat_sessions, user_org_memberships`) have `ENABLE` + `FORCE ROW LEVEL SECURITY`
with four policies each (`tenant_isolation_{select,insert,update,delete}`):

```sql
org_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid
```

The `nullif(..., '')` hardening (migration `002`) makes an unset/empty GUC normalize
to NULL → zero rows / blocked writes, rather than raising on an empty `::uuid` cast
(fail-closed and error-free). `orgs` and `user_profiles` are **not** RLS-scoped
(they cross tenants by design); junction tables are not RLS-scoped but are granted
to `app_user`.

**Roles:** `app_user` (`NOBYPASSRLS`, request-time via `SET ROLE`) and `app_admin`
(`BYPASSRLS`, migrations/admin ops). Migration `007` idempotently ensures `app_user`
exists with grants (init-db.sql only runs on a fresh volume).

**Vector store (Qdrant).** Physically separate collections per tenant —
`{tenant_id}-chunks` and `{tenant_id}-documents` — with a named `embedding` vector
(cosine). Payloads also carry `tenant_id` and `access_keys` for belt-and-braces
filtering and folder/tag scoping (`MatchAny`).

**Graph store (Neo4j).** Single database; tenant isolation by **label**
(`:Entity:Tenant_<sanitized_id>`). Every match is label-scoped; relationships carry
`tenant_id`, `access_keys`, `tags`, and `document_key`. Requires the APOC plugin.

---

## 7. Security Boundaries

Three distinct, non-overlapping secrets guard three trust boundaries:

| Boundary | Header / secret | Enforcement |
|----------|-----------------|-------------|
| Browser → API | `Authorization: Bearer <Clerk JWT>` | RS256 + issuer pin + **default-deny `azp` allowlist** (Clerk tokens have no `aud`) |
| API → Brain API | `X-API-Key: <BRAIN_API_KEY>` | Required; missing config → 503, mismatch → 401 |
| Worker → API (internal) | `X-Internal-API-Key: <INTERNAL_API_KEY>` | **Constant-time** compare (`hmac.compare_digest`); empty key → 503 |

Authorization tiers: **site admin** (`user_profiles.is_site_admin`) ⊃ **org admin**
(`user_org_memberships.is_org_admin`) ⊃ **member** (mask-gated). Retrieval is
entitlement-filtered by 32-bit access masks *before* any content reaches the LLM.
The E2E header-auth bypass (`X-Test-User`/`X-Test-Secret`) is gated by
`e2e_test_mode` and must never be enabled in production.

---

## 8. Infrastructure & Deployment

Compose files live in `docker/`; `docker-compose.infra.yml` is the shared base
`include`d by the others.

| Component | Image | Ports (host→cluster) | Notes |
|-----------|-------|----------------------|-------|
| PostgreSQL | `postgres:18` | **5433**→5432 | RLS; `init-db.sql` seeds roles |
| Redis | `redis:7.4-alpine` | 6379 | Celery broker/result + setup token |
| Qdrant | `qdrant/qdrant:v1.12.4` | 6333 | Vector DB (no healthcheck in image) |
| Neo4j | `neo4j:5.25.1` | 7474 / 7687 | Knowledge graph + APOC |
| MinIO | `minio/minio:…` | 9000 / 9001 | Object storage for originals; bucket auto-created |
| API | `docker/api.Dockerfile` | 8000 | FastAPI (Python) |
| Brain API | `docker/brain-api.Dockerfile` | 8020 | FastAPI (Python) |
| Worker | `docker/worker.Dockerfile` | — | Celery + Tesseract/poppler/antiword |
| Flower | `mher/flower:2.0` | 5555 | Celery task monitor |
| UI | `docker/ui.Dockerfile` | 3000 | Next.js standalone |
| pgAdmin | `dpage/pgadmin4:8.12` | 81→80 | Profile `dev-tools` only |

**Stacks:** `make dev` → Python (`docker-compose.yml` [+ `.dev.yml` for reload]);
`make dev-go` → Go (`docker-compose.go.yml`); `docker-compose.prod.yml` → self-contained
Python prod template (pinned tags, resource limits, strict healthchecks, required
secrets via `${VAR:?}`, no pgAdmin/Flower). **`run-stack.sh`** is the primary hybrid
dev launcher: dockerized infra + Python Brain/worker, with host `uvicorn` (no reload,
`:8000`) and `next dev` (`:3000`) reading `.env.host`.

**Migrations:** Alembic for the Python API (`make migrate` → `alembic upgrade head`,
revisions `001`–`007`); golang-migrate for the Go port (`make go-migrate`). See
[DATABASE.md](DATABASE.md) and [DEPLOYMENT.md](DEPLOYMENT.md).

> **Doc-vs-reality notes:** Postgres publishes host port **5433** (older docs say
> 5432); `make go-migrate` assumes 5432 and is a known footgun. Flower starts with
> `make dev` even though it is framed as dev-only. `.env.host` may still contain
> stale `KEYCLOAK_*` vars post-cutover — remove them.

---

## 9. Go Migration Status

A parallel Go rewrite exists (`go.work`, Go 1.25) as a well-tested port of the
CRUD/ingest/search core, but is **not yet authoritative**:

- **Wired only** into `docker-compose.go.yml` (`make dev-go`); absent from prod and `run-stack.sh`.
- **Missing surfaces** vs Python: chat/RAG (`/ask`), search proxy, first-run setup
  wizard, site-admin/`admin` console, `auth`, and `attributes`.
- **Incompatible queue:** `api-go`/`worker-go` use **asynq** (Redis) task types;
  the Python stack uses **Celery**. The two pipelines cannot be mixed — run one full
  stack or the other.
- CI runs `go test -race -cover` but with **no coverage gate**; the 80% gate is Python-only.

**Bottom line: Python is the shipping stack.** Track Go completeness before any cutover.

---

## 10. Observability

All services emit single-line **JSON logs** with `trace_id`/`span_id` injected from
the active OpenTelemetry span, and expose **`/healthz`**. When
`OTEL_EXPORTER_OTLP_ENDPOINT` is set, OTLP gRPC exporters ship traces and metrics
(the Brain API and API also install a Prometheus instrumentator exposing `/metrics`).
Brain metrics include `brain_chunks_ingested_total`, `brain_triplets_ingested_total`,
`brain_ingest_duration_ms`, and `brain_search_duration_ms` (tagged by `tenant_id`).
`RequestLoggingMiddleware` assigns/propagates `X-Request-ID`. Flower (5555) monitors
Celery; pgAdmin (81, dev-tools profile) inspects the database.

> `/readyz` on the Python API is currently a static stub (REDARCH-12); real
> dependency probes are deferred. The Go services implement deeper `/readyz` checks.
