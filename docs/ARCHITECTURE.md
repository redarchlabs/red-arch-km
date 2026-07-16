# Architecture

Red Arch Knowledge Management Platform v2 (KM2) is a multi-tenant, AI-assisted
enterprise knowledge platform: RAG chat and semantic search over ingested
documents, a knowledge graph, schema-driven custom entities with workflow
automation, an AI agent organization, a versioned enterprise API, and
release-promotion between environments — all gated by fine-grained RBAC and
PostgreSQL Row-Level Security. This document maps the services, data flows, and
trust boundaries for engineers and technical evaluators; it links to sibling docs
that own each subsystem in depth.

> **Which stack ships?** The **Python** implementation (`services/api`,
> `services/brain_api`, `services/worker`, `ui/`) is authoritative — it is what
> `run-stack.sh`, `make dev`, and `docker/docker-compose.prod.yml` run. A parallel
> **Go** rewrite (`services/api-go`, `services/brain-api-go`, `services/worker-go`)
> is an in-progress port wired only into `docker/docker-compose.go.yml` — see
> [§10 Go Migration Status](#10-go-migration-status). This document describes the
> Python stack unless stated otherwise.

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Services](#2-services)
3. [Shared Packages](#3-shared-packages)
4. [Request, Auth & Tenancy Model](#4-request-auth--tenancy-model)
5. [Data Flows](#5-data-flows)
6. [Multi-Tenancy & Isolation](#6-multi-tenancy--isolation)
7. [Security Boundaries](#7-security-boundaries)
8. [Platform Surfaces](#8-platform-surfaces)
9. [Infrastructure & Deployment](#9-infrastructure--deployment)
10. [Go Migration Status](#10-go-migration-status)
11. [Observability](#11-observability)
12. [Known gaps / TODO](#12-known-gaps--todo)

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
                    Bearer JWT + X-Org-ID │  (SSE for chat/agents)
                                     │            km2_… API key ──► /api/v1
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
                                  │ Celery      │───────────┘
                                  │ Worker+Beat │──► MinIO/S3 (9000, originals)
                                  └─────────────┘
                     internal callback (X-Internal-API-Key) ──► API /api/internal/*
```

The UI holds no server session — it attaches a Clerk Bearer token and an
`X-Org-ID` header to every call. The API (`services/api`) enforces RBAC + RLS,
owns the relational data, runs the workflow and agent engines, and delegates all
AI/ML work to the Brain API (`services/brain_api`) and heavy document processing
to Celery workers (`services/worker`). The Brain API owns the vector store
(Qdrant) and knowledge graph (Neo4j). Celery **Beat** drives all periodic work
(workflow outbox sweep, timers, agent runs) by calling the API's internal
endpoints, which do the RLS-scoped work.

---

## 2. Services

### 2.1 API Service — `services/api` (port 8000)

FastAPI + async SQLAlchemy. App factory `create_app` (`src/api/main.py`), title
"Red Arch Knowledge Management API". Responsibilities:

- Clerk session-JWT verification and user auto-provisioning
  (`auth/dependencies.py`, `auth/clerk.py`, `services/user_provisioning.py`).
- Multi-tenant CRUD: orgs, users, memberships, dimensions, folders, tags,
  attributes, documents, chat sessions.
- RLS enforcement (drop to `app_user`, pin `app.current_tenant_id`) plus explicit
  `org_id` repository filtering — see [§4](#4-request-auth--tenancy-model).
- RBAC via 32-bit access masks (`packages/access_mask`) and per-entity/per-field
  access policy (`repositories/dynamic_entity.py`) — see [RBAC.md](RBAC.md).
- Schema-driven custom entities (`ce_*` tables), forms, views, and the reporting
  engine.
- Workflow engine (token-based BPMN executor) driven by an outbox + Celery Beat —
  see [WORKFLOW_ENGINE.md](WORKFLOW_ENGINE.md).
- Document ingestion dispatch to Celery; status callbacks via the internal router.
- RAG/search **proxy** to the Brain API (including SSE pass-through).
- The **agent org** runtime, first-run setup wizard, site-admin console,
  change-management/promotion, and the enterprise `/api/v1` surface.

**Layout:** `routers/` (HTTP), `models/` (SQLAlchemy ORM + RLS), `repositories/`
(org-scoped data access), `services/` (business logic), `auth/` (Clerk + API-key
+ dependencies), `middleware/` (request logging), `tasks/` (Celery dispatch
signatures), `alembic/` (migrations).

**Routers mounted** (`main.py`, all under `/api/*` unless noted):

| Prefix | Router(s) | Purpose |
|--------|-----------|---------|
| `/` | `health` | `/healthz`, `/readyz` |
| `/api/auth`, `/api/users`, `/api/orgs`, `/api/memberships` | auth, users, orgs, memberships | identity + membership |
| `/api/dimensions`, `/api/attributes` | dimensions, attributes | RBAC dimensions (regions/roles/groups/departments) |
| `/api/documents`, `/api/folders`, `/api/tags` | documents, folders, tags | knowledge base |
| `/api/chat`, `/api/search` | chat, search | chat sessions + RAG/search proxy |
| `/api/entity-definitions`, `/api/entities` | entity_definitions, entity_records | custom entities + records |
| `/api/workflows`, `/api/forms`, `/api/views`, `/api/reports` | workflows, forms, views, reports | automation, forms, views, reports |
| `/api/public/forms`, `/api/inbound` | forms (public), inbound | unauthenticated form fill + token-auth inbound webhooks |
| `/api/agent`, `/api/agents` | agent, agent_console, mcp_servers, agent_approvals, agents | assistant + agent org (see [AGENT_ORG.md](AGENT_ORG.md)) |
| `/api/work-orders` | work_orders | agent work orders |
| `/api/internal` | internal | worker/beat callbacks (`X-Internal-API-Key`) |
| `/api/setup`, `/api/admin` | setup, admin | first-run wizard, site-admin console |
| `/api/migration`, `/api/promotions` | migration, promotions | import/export + release promotion ([CHANGE_MANAGEMENT.md](CHANGE_MANAGEMENT.md)) |
| `/api/api-keys` | api_keys | org-admin API-key management |
| `/api/v1` | `v1/` (entities, records, reports, workflows, search, knowledge, agents, config) | API-key-authenticated enterprise surface ([API.md](API.md)) |

Full endpoint reference: [API.md](API.md). Router-mount order is load-bearing:
`agent_console`/`mcp_servers`/`agent_approvals` register before `agents.router`
so its `GET /{agent_id}` cannot shadow their literal single-segment paths.

### 2.2 Brain API — `services/brain_api` (port 8020)

FastAPI (`create_app` in `src/brain_api/main.py`, title "Red Arch Brain API"). All
AI/ML operations; authenticated service-to-service via `X-API-Key`. Blocking
clients (Qdrant/Neo4j/OpenAI) run off the event loop via `asyncio.to_thread`.
Startup eagerly initializes stores and warms the query path.

- **Ingest** (`routers/ingest.py`, `/api/*`): chunk → embed + summarize → upsert
  vectors → document summary/tree → optional reified-claim extraction → Neo4j.
  Also `remove-document`, `update-document-metadata`, `init-tenant`,
  `remove-tenant`, and paginated chunk/summary reads.
- **Search** (`routers/search.py`, `/api/*`): `vector-search` and `vector-chat`
  (hybrid RAG, streaming and non-streaming).
- **RAG** (`routers/rag.py`, `/api/v1/*`): `ask` and `ask/stream` (SSE with
  `sources`/`graph`/`delta`/`done`/`error` events).
- **Agent** (`routers/agent.py`, `/api/v1/*`): agentic (tool-using) RAG that the
  API proxies for `/api/search/chat/agent[/stream]`.

The reified-claim knowledge engine (`packages/brain_sdk/facts`) is gated by
`USE_FACT_ENGINE`; when on, the store ensures the fact schema at startup. See
[KNOWLEDGE_ENGINE.md](KNOWLEDGE_ENGINE.md).

### 2.3 Worker — `services/worker` (Celery + Beat, Redis broker)

Background processing, `task_acks_late=True`, `prefetch_multiplier=1`. Tasks
(`src/worker/tasks/`):

- `task_ingest_document` (`ingest.py`) — text-only docs → POST to Brain.
- `task_extract_and_ingest` (`extract.py`) — uploaded files: fetch original from
  object storage → extract text (`.txt/.md` direct, `.docx` mammoth, `.doc`
  antiword, PDF/image Tesseract or OpenAI vision) → POST to Brain.
- `task_update_document_metadata` (`metadata.py`) — re-propagate
  tags/access-keys/title to vectors.
- **Beat-driven poll tasks** (`workflow.py`, `agents.py`) that POST to the API's
  internal endpoints, which perform the RLS-scoped work.

Beat schedule (`celery_app.py`, intervals overridable by env var):

| Schedule name | Task → internal endpoint | Default interval |
|---------------|--------------------------|------------------|
| `workflow-sweep-outbox` | `POST /api/internal/workflows/dispatch-batch` | 10 s |
| `workflow-run-timers` | `POST /api/internal/workflows/run-timers` | 30 s |
| `workflow-advance-tokens` | `POST /api/internal/workflows/advance-tokens` (BPMN token engine) | 10 s |
| `workflow-maintain-partitions` | `POST /api/internal/workflows/maintain-partitions` | 86400 s |
| `agents-advance-runs` | `POST /api/internal/agents/advance-runs` | 10 s |
| `agents-run-schedules` | `POST /api/internal/agents/run-schedules` | 30 s |
| `beat-heartbeat` | `worker.tasks.monitoring.beat_heartbeat` (liveness beacon for site-admin) | 15 s |

Shared `_ingest_common` retries only on 5xx/429/network and posts a best-effort
status callback to the API's internal router. The worker image bundles
`tesseract-ocr`, `poppler-utils`, and `antiword`.

### 2.4 UI — `ui/` (Next.js 15, port 3000)

App Router, React 18, TypeScript, Tailwind v4. Auth via `@clerk/nextjs`. **No React
Query** — data fetching is imperative via an axios singleton (`lib/api/client.ts`)
plus native `fetch` for SSE. State via React Context (`Auth`, `Org`, `Theme`,
`Help`). The axios interceptor attaches `Authorization` and `X-Org-ID`, where a
per-request `X-Org-ID` wins over the ambient org (used by the cross-org
site-admin console).

Key capabilities: streaming chat with scope selector and citations; a two-pane
Explorer resource browser (virtualized via `react-window`); document upload with
OCR/AI extraction; a CodeMirror Markdown editor; a scroll-synced reader; the
form/view builder and workflow editor; the agent console; the change-management
console; a context-sensitive help dock; and Light/Dark/Red Arch themes. Forms and
views are described in [FORMS_AND_VIEWS.md](FORMS_AND_VIEWS.md).

### 2.5 Agent Org — `services/api/src/api/services/agents`

A multi-tenant **agent organization**: org charts of AI agents (kinds
`coordinator`/`advisory`/`operator`) that plan, delegate, and act on the org's own
data, governed by a `deny > ask > allow` authority engine (`authority.py`,
`kind_gate.py`) with a central high-touch approval inbox (`approvals.py`,
`orgs.agent_autonomy`). The provider-agnostic runtime (`runtime.py`) drives any
model via **LiteLLM** through two paths: the **interactive console**
(`console.py`, in-process SSE, auto-approves with the human present) and the
**worker executor** (`run_executor.py`, claims queued runs, parks side-effecting
actions for async approval). A cron **scheduler** (`scheduler.py`) enqueues runs;
agents reach external tools over **MCP** (`mcp/`) via a per-org/per-user OAuth
"Connect" flow. Migrations 030–033 back the domain. Full reference:
[AGENT_ORG.md](AGENT_ORG.md).

### 2.6 Go rewrite — `services/{api-go,brain-api-go,worker-go}`

In-progress port; not authoritative. See [§10](#10-go-migration-status).

---

## 3. Shared Packages

| Package | Purpose |
|---------|---------|
| `packages/access_mask` | Pure 32-bit RBAC mask: `encode`/`decode`/`matches` over layout `[org:11][region:5][role:5][group:7][dept:4]` (constants in `constants.py`). Used to compile folder/document permissions and user entitlements |
| `packages/brain_sdk` | AI/ML primitives: sentence-aware chunker (`o200k_base`), OpenAI embedding provider, hierarchical `ChunkSummarizer`, claim/triplet extraction, the reified-claim fact store (`facts/`, Neo4j-backed, tenant-scoped), and the Qdrant vector-store + Neo4j graph-store abstractions |
| `packages/shared_config` | Pydantic settings (DB/Redis/OpenAI/observability), JSON logging with OTel trace correlation, and OTLP telemetry setup |
| `packages/accessmask` | **Go** counterpart of `access_mask` |
| `packages/shared` | **Go** shared `logging` + `telemetry` |

---

## 4. Request, Auth & Tenancy Model

Every authenticated browser request carries a Clerk **Bearer JWT** and (for
tenant-scoped endpoints) an **`X-Org-ID`** header. There is no server-side
session. Auth verification lives in `auth/dependencies.py`:

- `get_current_user` — verifies the JWT against the pinned Clerk issuer (JWKS
  RS256 + issuer + `azp` allowlist, `auth/clerk.py`), then auto-provisions the
  `UserProfile`. An E2E header bypass (`X-Test-User`/`X-Test-Secret`) is gated by
  `e2e_test_mode` and never enabled in production.
- `require_org_access` → `OrgContext` (membership + dimensions; site admins get a
  synthetic org-admin membership) → `require_org_admin` / `require_site_admin` /
  `require_internal_api_key`.

The app connects to Postgres as a **non-superuser role, `km_app`** (migration
035), so RLS applies to it. Access mode is chosen per transaction by the
`SET LOCAL` helpers in `db_scope.py`, backed by the two FastAPI session
dependencies in `dependencies.py`:

| Dependency | `db_scope` call | Behavior |
|------------|-----------------|----------|
| `get_tenant_db` (tenant-scoped endpoints) | `enter_tenant(session, org_id)` | `SET LOCAL ROLE app_user`, `app.bypass='off'`, `set_config('app.current_tenant_id', org_id)`. RLS scopes every statement to one org. Also pins `TIME ZONE 'UTC'` and a 30s `statement_timeout` |
| `get_db` (cross-org / no-tenant endpoints) | `enter_bypass(session)` | `SET LOCAL app.bypass='on'` → the permissive `admin_bypass_all` policy (migration 034) widens visibility to every org for reads **and** writes (auth membership lookups, site-admin, provisioning, token→org resolution) |

`db_scope` also provides `enter_tenant_owner` (stays on `km_app` for org-scoped
work that must run DDL — e.g. the config assistant / agent executor authoring
`ce_*` tables) and `exit_to_bypass` (return a per-tenant sweep unit to cross-org
mode). Everything uses `SET LOCAL` / `set_config(..., true)`, so it is
transaction-scoped and auto-reverts on commit/rollback — pooled connections stay
clean. The GUC defaults to unset, so a session that sets **neither** mode fails
closed (RLS with no tenant GUC returns zero rows).

> This GUC-plus-policy model replaced an earlier one where the base connection was
> the Postgres superuser (`BYPASSRLS`). The security posture is identical, but it
> runs on managed Postgres (e.g. Google Cloud SQL, whose `cloudsqlsuperuser`
> cannot hold `BYPASSRLS`). See [DATABASE.md](DATABASE.md).

Full auth reference: [AUTHENTICATION.md](AUTHENTICATION.md) and [RBAC.md](RBAC.md).

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
                                              │        ├─ chunk → embed → Qdrant
                                              │        ├─ hierarchical summaries + tree
                                              │        └─ (if enabled) claims → Neo4j
                                              └─ POST status callback ──► API /api/internal/*
                                                                          └─ status=SUCCESS/FAILED
```

Re-ingest (content replace) purges existing vectors first, because Brain ingest is
not idempotent (fresh UUIDs per run). Ingest is async (202 + background job +
poll) to avoid whole-document HTTP timeouts.

### 5.2 RAG Query (streaming)

```
UI ──POST /api/search/chat/stream (fetch, SSE)──► API
     │  computes user access masks (org admin → unrestricted)
     │  maps selected folder_ids → folder:<id> tags (OR); free tags (AND)
     └─ proxy ──► Brain /api/v1/ask/stream
                   ├─ embed query → Qdrant search (access_keys + tags filter, top-5)
                   ├─ (optional) knowledge-graph fact lookup (RBAC-filtered)
                   ├─ dedupe to unique source documents
                   ├─ build context → OpenAI chat (answer only from context, cite [n])
                   └─ stream events: sources → graph → delta… → done
     ◄─ SSE bytes forwarded verbatim ─┘
UI renders tokens incrementally, turns [n] into citation links, can AbortController-cancel
```

Passage-level citations (snippet + section + deep-link) are carried in the
`sources` event. An agentic (tool-using) variant proxies `POST
/api/search/chat/agent/stream` to the Brain agent router.

### 5.3 Custom Entities (Schema-Driven Records)

```
UI ──POST /api/entity-definitions──► API
                                     ├─ validate slug (no reserved words)
                                     ├─ insert catalog rows (migration 008)
                                     └─ run physical DDL ──► PostgreSQL
                                        CREATE TABLE ce_<slug> (... org_id UUID ...)
                                        ENABLE + FORCE ROW LEVEL SECURITY

UI ──GET /api/entities/{slug}/records──► API
                                         ├─ resolve entity_definition + fields from catalog
                                         ├─ keyset-paginated query (cursor = (created_at, id))
                                         ├─ RLS-enforced read from ce_<slug>
                                         └─ optional full-text search ──► pg_trgm (migration 010)
```

Reads/writes go through `DynamicEntityRepository`, which enforces the per-entity
write policy and per-field read policy (migration 039) — a non-`privileged`
caller cannot write a `workflow_only` entity (`RecordAccessError` → 403) or
read/filter a `server_only` field. The workflow engine and org admins run
`privileged`. See [RBAC.md](RBAC.md). Record create/update/delete writes to
`workflow_outbox` in the same transaction (at-least-once), triggering automations.

### 5.4 Workflow Automation (Poll-Based Dispatch)

```
Entity record change ──► DynamicEntityRepository ──► PostgreSQL (same txn)
                        └─ INSERT into workflow_outbox

Celery Beat (workflow-sweep-outbox, 10s)
  └─ POST /api/internal/workflows/dispatch-batch (X-Internal-API-Key)
       └─ API ── FOR UPDATE SKIP LOCKED ──► SELECT workflow_outbox WHERE status='pending'
                 ├─ claim event; match workflows (entity + trigger)
                 ├─ INSERT workflow_run + steps
                 └─ per-step: drop to app_user (RLS-scoped) and execute actions
                    (update_record_field, send_email, send_webhook [SSRF-allowlisted],
                     send_form, create_record, knowledge_search, llm_* actions, …)

Time-based (workflow-run-timers 30s, workflow-advance-tokens 10s)
  └─ resume delayed runs (delay_until <= now, pg_advisory_lock) + drive the
     BPMN token engine (reactivate parked tokens/timers/retries)
```

The engine is a BPMN 2.0 token executor (migration 018); most v2 runs finish
synchronously in `dispatch-batch`, and the token sweep resumes waits. Inbound
webhook runs (migration 020/022) execute inline. `workflow_outbox`,
`workflow_runs`, and `workflow_run_steps` are RANGE-partitioned by `created_at`
(monthly); `maintain-partitions` pre-creates upcoming partitions daily. Full
detail: [WORKFLOW_ENGINE.md](WORKFLOW_ENGINE.md).

### 5.5 Intake Forms (Token-Linked Public Collection)

```
UI (admin) ──POST /api/forms/{id}/links──► API  (INSERT form_links, token_hash = SHA-256)
External user
  ├─ GET  /api/public/forms/{token}   (unauth; org resolved from token_hash before RLS)
  └─ POST /api/public/forms/{token}   (validate pending→submitted + expiry;
                                       write ce_<slug>; INSERT workflow_outbox source='form_submission')
```

`form_links.token_hash` is globally unique and indexed (public resolution before
tenant context); the raw token is shown only at creation and never stored
(migration 011).

---

## 6. Multi-Tenancy & Isolation

**PostgreSQL RLS.** Tenant tables have `ENABLE` + `FORCE ROW LEVEL SECURITY` with
per-operation policies plus the permissive cross-org `admin_bypass_all` policy
(migration 034). Scoped domains include permission/org tables (regions,
departments, roles, groups, user_org_memberships), documents (folders, tags,
documents + per-doc permission columns, chat_sessions), custom entities
(entity_definitions, entity_fields, entity_relationships, `ce_*`), workflows
(workflows, workflow_versions, workflow_outbox, workflow_runs, workflow_run_steps),
forms (forms, form_links), views, reports, api_keys, and the agent-org tables.

```sql
-- tenant policy predicate
org_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid
-- bypass policy (migration 034)
current_setting('app.bypass', true) = 'on'
```

The `nullif(..., '')` hardening (migration 002) normalizes an unset/empty GUC to
NULL → zero rows / blocked writes, rather than raising on an empty `::uuid` cast
(fail-closed and error-free). `orgs` and `user_profiles` are **not** RLS-scoped
(they cross tenants by design). **Roles:** `app_user` (`NOBYPASSRLS`, request-time
via `SET ROLE`), `km_app` (the app's base non-superuser connection role, migration
035), and `app_admin` (`BYPASSRLS`, migrations/admin ops). Migration 007
idempotently ensures `app_user` with grants.

**Vector store (Qdrant).** Physically separate collections per tenant —
`{tenant_id}-chunks` and `{tenant_id}-documents` — with a named `embedding` vector
(cosine). Payloads carry `tenant_id` and `access_keys` for belt-and-braces
filtering and folder/tag scoping.

**Graph store (Neo4j).** Single database; tenant isolation by **label**
(`:Entity:Tenant_<sanitized_id>`). Every match is label-scoped; relationships carry
`tenant_id`, `access_keys`, `tags`, and `document_key`. Requires the APOC plugin.

See [DATABASE.md](DATABASE.md) for the full table/migration reference.

---

## 7. Security Boundaries

Distinct, non-overlapping secrets guard each trust boundary:

| Boundary | Header / secret | Enforcement |
|----------|-----------------|-------------|
| Browser → API | `Authorization: Bearer <Clerk JWT>` + `X-Org-ID` | RS256 + issuer pin + default-deny `azp` allowlist (`auth/clerk.py`) |
| External caller → `/api/v1` | `Authorization: Bearer km2_…` or `X-API-Key` | SHA-256 hash lookup on a short-lived privileged session; scope-gated; opaque 401 (`auth/api_key.py`, migration 028) |
| API → Brain API | `X-API-Key: <BRAIN_API_KEY>` | Required; missing config → 503, mismatch → 401 |
| Worker/Beat → API (internal) | `X-Internal-API-Key: <INTERNAL_API_KEY>` | Constant-time compare (`hmac.compare_digest`); empty key → 503 |
| Per-org secrets (at-rest) | `ORG_ENCRYPTION_KEY` (Fernet) | Per-org provider keys encrypted/decrypted (migrations 016/029); never logged |

Authorization tiers: **site admin** (`user_profiles.is_site_admin`) ⊃ **org
admin** (`user_org_memberships.is_org_admin`) ⊃ **member** (mask-gated). Retrieval
is entitlement-filtered by 32-bit access masks *before* any content reaches the
LLM.

**Workflow security:** manual runs validate `record_id` ownership and reject
side-effecting actions on free-form client data; webhook targets are validated
against `WORKFLOW_WEBHOOK_ALLOWLIST` (empty list disables webhooks); exactly-once
dispatch via `FOR UPDATE SKIP LOCKED` on the outbox + `pg_advisory_lock` on
scheduled runs.

**Enterprise API:** `config:write` (remote-controls an org's whole configuration)
is a `SENSITIVE_SCOPES` grant — never satisfied by a `*` or `config:*` wildcard, so
it must be minted explicitly (`services/api_key_scopes.py`). Per-key and per-IP
rate limits are enforced in Redis. Outbound configuration pushes are SSRF-guarded.

**Agent org:** every side-effecting agent action passes the `deny > ask > allow`
authority engine; `ask` parks the action in the approval inbox; the interactive
console auto-approves only while a human is present.

---

## 8. Platform Surfaces

Beyond the browser app, KM2 exposes several first-class surfaces. Each is owned by
a sibling doc; this section is the map.

- **Enterprise REST API (`/api/v1`).** Versioned, API-key-authenticated surface
  for entities, records, reports, workflows, search, knowledge, agents, and config
  (`routers/v1/`). Keys are org-scoped, SHA-256 hashed (`km2_…`, migration 028),
  minted with explicit scopes, and rate-limited. Reference: [API.md](API.md);
  auth model: [AUTHENTICATION.md](AUTHENTICATION.md).
- **Entity access control (RBAC).** 32-bit access masks for
  folder/document/retrieval entitlement, plus per-entity write and per-field read
  policies that make records tamper-proof (migration 039). Reference:
  [RBAC.md](RBAC.md).
- **Change management (release promotion).** Config lineage (migration 037) +
  release/promotion tables (migration 038) let an org export a versioned bundle,
  diff it against a target, and promote it to another environment. Backend in
  `services/migration/` (bundle, diff, exporter, importer, promotion, deleter,
  inflight, transport); routers `migration.py` + `promotions.py`; UI in
  `ui/src/components/change-management/`. Reference:
  [CHANGE_MANAGEMENT.md](CHANGE_MANAGEMENT.md).
- **AI agent org.** Multi-tenant agent runtime with governance, scheduling, and
  MCP tool access. Reference: [AGENT_ORG.md](AGENT_ORG.md).
- **MCP & integrations.** `tools/km2-mcp` is a stdio MCP server that drives the
  KM2 API on a user's behalf by harvesting a fresh Clerk token from a persistent
  Playwright browser session (no stored secrets; RLS still enforced server-side).
  Registered via the repo `.mcp.json`. The agent org additionally consumes
  **outbound** MCP servers via per-org OAuth. Reference:
  [MCP_AND_INTEGRATIONS.md](MCP_AND_INTEGRATIONS.md).

---

## 9. Infrastructure & Deployment

Compose files live in `docker/`; `docker-compose.infra.yml` is the shared base
`include`d by the others.

| Component | Image | Ports (host→cluster) | Notes |
|-----------|-------|----------------------|-------|
| PostgreSQL | `postgres:18` | **5433**→5432 | RLS; `init-db.sql` seeds roles (incl. `km_app`) |
| Redis | `redis:7.4-alpine` | 6379 | Celery broker/result + rate limits + advisory locks |
| Qdrant | `qdrant/qdrant:v1.12.4` | 6333 | Vector DB (no in-image healthcheck; dependants use `service_started`) |
| Neo4j | `neo4j:5.25.1` | 7474 / 7687 | Knowledge graph + APOC; Bolt TCP healthcheck |
| MinIO | `minio/minio:RELEASE.2024-10-13…` | 9000 / 9001 | Object storage for originals; `createbuckets` init job |
| Mailpit | `axllent/mailpit:v1.21` | 1025 / 8025 | Dev SMTP catcher (intake-form email testing) |
| API | `docker/api.Dockerfile` | 8000 | FastAPI (Python) |
| Brain API | `docker/brain-api.Dockerfile` | 8020 | FastAPI (Python) |
| Worker | `docker/worker.Dockerfile` | — | Celery worker; Tesseract/poppler/antiword |
| Beat | `docker/worker.Dockerfile` | — | Celery Beat; workflow/agent poll + partition maintenance |
| Flower | `mher/flower:2.0` | 5555 | Celery task monitor |
| UI | `docker/ui.Dockerfile` | 3000 | Next.js standalone |
| pgAdmin | `dpage/pgadmin4` | 81→80 | Profile `dev-tools` only |

**Stacks:**

- `make dev` → `docker/docker-compose.dev.yml` (includes the base + infra, adds
  source bind-mounts + `uvicorn --reload` for api/brain-api). Full Python stack.
- `make dev-go` → `docker/docker-compose.go.yml` (Go services on the same ports:
  api-go 8000, brain-api-go 8020, worker-go, plus the Python UI).
- `run-stack.sh` — the primary **hybrid** dev launcher: dockerized infra +
  Python Brain/worker/beat, with host `uvicorn` (`:8000`, no reload) and
  `next dev` (`:3000`).
- `docker/docker-compose.prod.yml` — self-contained Python prod template (pinned
  registry tags via `${REGISTRY}`/`${TAG}`, resource limits, strict healthchecks,
  required secrets via `${VAR:?}`). It ships api, brain-api, worker, celery-beat,
  ui, postgres, redis, qdrant, neo4j — **not** MinIO, Mailpit, Flower, or pgAdmin,
  so a prod deploy provides object storage via `STORAGE_*` / an external
  S3-compatible endpoint (or a compose override). See [DEPLOYMENT.md](DEPLOYMENT.md).

**Migrations:** Alembic for the Python API — `make migrate` runs `alembic upgrade
head` (currently **039**) against `localhost:5433`. golang-migrate drives the Go
port — `make go-migrate` (`services/api-go/migrations/`). See
[DATABASE.md](DATABASE.md).

> **Footguns:** `make go-migrate` targets `localhost:5432` (`DATABASE_URL_GO`) but
> the infra publishes Postgres on host **5433** — export the right URL or it hits
> the wrong (or no) database. Flower and Mailpit start with `make dev` even though
> they are dev-only.

---

## 10. Go Migration Status

A parallel Go rewrite (`go.work`, Go 1.25) is a partial, well-tested port of the
CRUD/ingest core, **not yet authoritative**:

- **Wired only** into `docker/docker-compose.go.yml` (`make dev-go`); absent from
  prod and `run-stack.sh`.
- **Implemented in `api-go`:** Clerk-JWT + API-key + `X-Org-ID` + internal-key
  middleware (`internal/middleware/`), a permissions service, a Brain client, and
  CRUD handlers for health, orgs, users, memberships, dimensions, folders, tags,
  documents, and internal callbacks.
- **Missing surfaces vs Python:** chat/RAG proxy, semantic search, custom entities
  + records, forms/views, reports, the workflow engine, the agent org, the
  enterprise `/api/v1` surface, migration/promotion, first-run setup, and the
  site-admin/`admin` console. `brain-api-go` (stores/pipeline/models handlers) and
  `worker-go` (tasks/handlers) exist as ports of ingest/search.
- **Incompatible queue:** `worker-go` uses **asynq** (`hibiken/asynq`, Redis)
  task types; the Python stack uses **Celery**. The two pipelines cannot be mixed
  — run one full stack or the other.
- CI runs `go test -race -cover` but with **no coverage gate**; the 80% gate is
  Python-only.

**Bottom line: Python is the shipping stack.** Track Go completeness before any
cutover.

---

## 11. Observability

All services emit single-line **JSON logs** with `trace_id`/`span_id` injected
from the active OpenTelemetry span, and expose **`/healthz`**. When
`OTEL_EXPORTER_OTLP_ENDPOINT` is set, OTLP exporters ship traces and metrics; the
API and Brain API also install a Prometheus instrumentator exposing `/metrics`.
Brain metrics are tagged by `tenant_id`. `RequestLoggingMiddleware` assigns and
propagates `X-Request-ID`. The site-admin console reads a Beat heartbeat
(`beat-heartbeat`, every 15 s) to prove beat → broker → worker liveness; a stale
heartbeat means Beat is down. Flower (5555) monitors Celery; pgAdmin (81,
`dev-tools` profile) inspects the database. Dev SMTP is captured by Mailpit
(8025); a site-admin "Sent Emails" tab proxies its API in dev/staging.

---

## 12. Known gaps / TODO

- Object storage is not bundled in `docker-compose.prod.yml`; production relies on
  an externally provided S3-compatible endpoint (or a compose override). Confirm
  the deployment's `STORAGE_*` configuration.

> Reviewed 2026-07-16 against Alembic migration 039. Source of truth is the code; if this doc disagrees with the code, the code wins.
