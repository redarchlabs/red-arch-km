# Changelog

All notable changes to the Red Arch Knowledge Management Platform are documented in
this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security

- **RED-3 — RLS fail-closed hardening.** Tenant-isolation RLS policies now
  normalise the tenant GUC with `nullif(current_setting('app.current_tenant_id',
  true), '')` before the `::uuid` cast. On a pooled connection a set-then-reverted
  GUC reads back as the empty string `''`; the previous bare `''::uuid` cast raised
  `invalid input syntax for type uuid` on the next RLS query (fail-closed but a 500
  instead of an empty result). The empty string now normalises to NULL, so an
  unset/empty tenant deterministically returns zero rows and blocks all writes —
  fail-closed and error-free. Applied to both the Python (`api`, Alembic migration
  `002_harden_rls_nullif`) and Go (`api-go`, migration `003_harden_rls_nullif`)
  schemas across all 44 `tenant_isolation_*` policies. Added integration regression
  tests for the empty-string GUC and for privileged (BYPASSRLS) cross-tenant access.

## [2.0.0] — 2026-06-14

First production release of the rebuilt Knowledge Management Platform. The rebuild
was delivered across eight phases. Each phase below lists its scope and the key
commits that delivered it.

### Phase 1 — Monorepo Scaffold & Foundations

- `uv`-managed Python monorepo with shared packages (`access_mask`, `brain_sdk`,
  `shared_config`) and three services (`api`, `brain_api`, `worker`).
- FastAPI application skeletons, SQLAlchemy async engine, Alembic migrations.
- Observability baseline: OpenTelemetry tracing, Prometheus metrics, structured
  JSON logging.
- Key commits: `7bf9b3f` (initial monorepo scaffold),
  `1be7575` (observability: OTel tracing, Prometheus metrics, JSON logging).

### Phase 2 — Core CRUD & Authentication

- JWT/OIDC authentication via Keycloak with mock-auth fallback for local dev.
- CRUD for Users, Orgs, Documents, Folders, and Tags.
- PostgreSQL Row-Level Security (RLS) for multi-tenant isolation
  (`app.current_tenant_id`).

### Phase 3 — Folder Hierarchy

- Folder tree with parent/child relationships and drag-and-drop reparenting.
- Cycle prevention and depth validation on folder moves.
- Key commit: `a42831e` (folders: hierarchy with drag-and-drop reparenting).

### Phase 4 — Brain API & Ingestion

- Brain API service for vector search, RAG, and graph context.
- `brain_sdk`: chunking, embedding, vector store (Qdrant) and graph store (Neo4j).
- Document ingestion pipeline with hierarchical summaries and triplet extraction.
- Key commit: `d036ab5` (ingest: gpt-5-mini, hierarchical summaries, parallel
  triplets).

### Phase 5 — Chat & RAG Pipeline

- Chat session CRUD with history persisted in `chat_data` JSONB.
- RAG endpoints (`/api/v1/ask`, `/api/v1/ask/stream`) with SSE streaming.
- API search proxy (`/api/search/chat`, `/api/search/chat/stream`) to Brain API.
- Citation generation and permission-scoped retrieval (32-bit `access_mask`).
- Implemented in Python using ideal native modules (FastAPI, SQLAlchemy, Pydantic,
  async `StreamingResponse`).

### Phase 6 — Admin & Membership Management

- Admin surfaces for tags, document attributes, and member/membership management.
- Org-deletion cascade propagated to Brain API resources.
- Key commits: `a173f48` (admin: tags, document attributes, memberships),
  `1dda4b4` (org-deletion cascade to brain-api + admin inline edit).

### Phase 7 — End-to-End & Security Testing

- Brain API integration tests, load tests, and seeded Playwright E2E journeys.
- RLS isolation tests, JWT/injection/RLS-bypass security validation.
- Multi-pass audit hardening (cascades, LIKE escaping, stream cancellation,
  input validation, pagination, async wrappers).
- 80%+ coverage target with CI enforcement.
- Key commits: `193aa0f` (testing: brain-api integration, load tests, E2E),
  `a88c1ba`, `65fa344`, `d14d98b`, `b406a21` (audit passes).

### Phase 8 — Deployment & Documentation

- Documentation suite: `ARCHITECTURE.md`, `DATABASE.md`, `RBAC.md`, `API.md`,
  `DEPLOYMENT.md`, `DEVELOPMENT.md`.
- Release files: `LICENSE` (Apache 2.0), `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`,
  `CHANGELOG.md`.
- Bootstrap fixes: observability wiring, Qdrant/UI healthchecks, membership
  relationship loading.
- Code cleanup via `ruff check --fix` and `ruff format`.
- Key commits: `66281a5` (bootstrap: observability wiring, healthchecks),
  `dd93366` (memberships: load relationships before assigning).

[2.0.0]: https://github.com/redarchlabs/red-arch-km-2/releases/tag/v2.0.0
