# Red Arch Knowledge Manager — Requirements

> **Status:** Reflects the **Python** implementation that ships today (`services/api`,
> `services/brain_api`, `services/worker`, `ui/`). A parallel Go rewrite
> (`services/*-go`) is in progress and **not yet authoritative** — see
> [§8 Constraints](#8-constraints) and [ARCHITECTURE.md](ARCHITECTURE.md).
> Verified against the codebase at branch `feat/site-admin-console` (2026-07).

Red Arch Knowledge Manager v2 is a multi-tenant, AI-powered enterprise knowledge
management platform combining Retrieval-Augmented Generation (RAG), vector search,
a knowledge graph, and fine-grained role-based access control (RBAC).

---

## Table of Contents

1. [Actors & Roles](#1-actors--roles)
2. [Glossary](#2-glossary)
3. [Functional Requirements](#3-functional-requirements)
4. [Non-Functional Requirements](#4-non-functional-requirements)
5. [External Dependencies](#5-external-dependencies)
6. [Configuration Requirements](#6-configuration-requirements)
7. [Known Limitations & Deferred Work](#7-known-limitations--deferred-work)
8. [Constraints](#8-constraints)

---

## 1. Actors & Roles

| Actor | Description | Privilege source |
|-------|-------------|------------------|
| **Anonymous visitor** | Unauthenticated browser; may only reach `/login`, `/sign-up`, and the public setup-status probe | — |
| **Member** | Authenticated user with a membership in ≥1 org; access gated by 32-bit permission masks | `user_org_memberships` + dimension assignments |
| **Org Admin** | Full control within a single org (folders, dimensions, attributes, tags, memberships) | `user_org_memberships.is_org_admin = true` |
| **Site Admin** | Instance-wide superuser; manages all orgs and users; receives a synthetic org-admin membership in every org | `user_profiles.is_site_admin = true` |
| **Worker (service)** | Celery background process; calls the API's internal router with a shared secret | `X-Internal-API-Key` |
| **Brain API (service)** | AI/ML service; called by the API with a shared secret | `X-API-Key` (`BRAIN_API_KEY`) |
| **Identity Provider** | Clerk (OIDC/OAuth2); issues session JWTs | External SaaS |

Privilege ordering: **Site Admin ⊃ Org Admin ⊃ Member ⊃ Anonymous**.

---

## 2. Glossary

| Term | Meaning |
|------|---------|
| **Access mask** | A 32-bit integer encoding `[org:11][region:5][role:5][group:7][dept:4]`, used to match a user's entitlements against a resource's permissions |
| **Wildcard** | The maximum value of a mask field (e.g. region=31), meaning "any value on this axis" — org is **never** a wildcard |
| **Permission config** | Human-readable JSON (`[{"region":"North","role":"Manager"}, …]`) that compiles to a list of access masks; entries are OR'd, dimensions within an entry are AND'd |
| **Dimension** | One of four org-scoped classification axes: **regions, departments, roles, groups** |
| **`dot_path`** | A materialized dot-separated path of folder names used for hierarchy queries; folder names may not contain `.` |
| **Chunk** | A ~500-token semantic segment of a document, embedded and stored in Qdrant |
| **Triplet** | A `(subject, predicate, object)` fact extracted from a chunk and stored in Neo4j |
| **`document_key`** | A per-org-unique UUID that links a Postgres document row to its vectors/graph nodes |
| **Tenant** | Synonymous with **org**; the isolation boundary for all data |

---

## 3. Functional Requirements

Priority key: **M** = Must Have, **S** = Should Have, **C** = Could Have.

### FR-1: Authentication & Identity

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-1.1 | The system shall authenticate end users via **Clerk** session JWTs (RS256, JWKS fetched from the issuer and cached ~300 s) | M |
| FR-1.2 | The system shall pin the token issuer to `CLERK_JWT_ISSUER` and re-verify it independently of any unverified header hint | M |
| FR-1.3 | Because Clerk session tokens carry no `aud`, the system shall enforce a **default-deny `azp` allowlist** (`CLERK_ALLOWED_AZP`); a token whose `azp` is missing or not allowlisted shall be rejected | M |
| FR-1.4 | The system shall fail startup if Clerk is configured without an `azp` allowlist | M |
| FR-1.5 | The system shall auto-provision a `user_profile` on first authenticated request (keyed on the token `sub` → `auth_subject`) and resync `username`/`email` from claims on every subsequent request | M |
| FR-1.6 | The system shall provision sub-derived fallbacks (`username=sub`, `email={sub}@placeholder.invalid`) when a token carries no username/email claim, avoiding unique-constraint collisions | M |
| FR-1.7 | The system shall reject authentication for a deactivated account (`is_active=false`) with HTTP 403 on both the Clerk and E2E auth paths | M |
| FR-1.8 | The system shall support an **E2E test auth bypass** (`X-Test-User` + `X-Test-Secret`) gated by `e2e_test_mode`, for automated tests only — never enabled in production | M |
| FR-1.9 | All main-API endpoints except health and `GET /api/setup/status` shall require a valid session | M |
| FR-1.10 | The UI shall gate routes both server-side (Clerk middleware `auth.protect()`) and client-side (redirect to `/login`) | M |

### FR-2: Multi-Tenancy & Organizations

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-2.1 | The system shall isolate all tenant data by org, enforced by **PostgreSQL Row-Level Security (RLS)** on the tenant tables and by explicit `org_id` filtering in every repository (defense in depth) | M |
| FR-2.2 | The system shall resolve the active tenant from a client-supplied `X-Org-ID` header (validated as a UUID; 400 on missing/invalid) and bind it to the RLS session GUC `app.current_tenant_id` per request | M |
| FR-2.3 | RLS policies shall **fail closed**: an unset/empty tenant GUC shall normalize to NULL and return zero rows / block writes (not raise a 500) | M |
| FR-2.4 | The system shall allow a user to belong to multiple orgs and switch between them without re-authenticating (client changes `X-Org-ID`; membership is re-verified each request) | M |
| FR-2.5 | The system shall allow site admins to create, read, update, and delete organizations | M |
| FR-2.6 | On org creation the system shall assign the next sequential `permission_number` (under a row lock) and best-effort initialize the tenant's Brain resources (Qdrant collections, Neo4j schema) | M |
| FR-2.7 | On org deletion the system shall cascade-delete all tenant rows in Postgres and best-effort purge the tenant's Qdrant collections and Neo4j nodes; Brain cleanup failures shall not block the Postgres delete | M |
| FR-2.8 | Each org shall support an optional per-org OpenAI API key and a `use_knowledge_graph` default flag | S |
| FR-2.9 | The UI shall present an org switcher (static label for a single org, dropdown for multiple) and persist the selection in `localStorage` | S |

### FR-3: First-Run Setup & Site Administration

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-3.1 | On boot with no active site admin, the API shall generate a **one-time setup token** (SHA-256 hash in Redis, 24 h TTL, single-use, never overwritten while unclaimed) and print it to its logs | M |
| FR-3.2 | The system shall expose `GET /api/setup/status` (unauthenticated) returning whether setup is needed | M |
| FR-3.3 | A signed-in user shall be able to claim global-admin at `/setup` by presenting the token (`POST /api/setup/claim`); invalid/used → 403, already-initialized → 409 | M |
| FR-3.4 | Orgless signed-in users shall be auto-redirected into the setup funnel | S |
| FR-3.5 | Site admins shall have an **Organizations** console (CRUD with type-to-confirm delete) | M |
| FR-3.6 | Site admins shall have a **Users** console: search, promote/demote site admin, deactivate/reactivate | M |
| FR-3.7 | Site admins shall have a cross-org **Memberships** console (add/remove members and toggle org-admin in any org via a per-request `X-Org-ID` override) | M |
| FR-3.8 | Site admins shall have a **System status** view (PostgreSQL, Redis, Brain API, Celery queue depth, API version, per-component latency) via `GET /api/admin/system` | S |
| FR-3.9 | The system shall prevent self-demotion, self-deactivation, and removal/deactivation of the **last active site admin** (guarded with an advisory lock; 400/409) | M |

### FR-4: Access Control (RBAC)

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-4.1 | The system shall support multi-dimensional permissions across four org-scoped axes: **regions, roles, groups, departments** | M |
| FR-4.2 | The system shall encode entitlements as **32-bit access masks** with layout `[org:11][region:5][role:5][group:7][dept:4]` | M |
| FR-4.3 | Mask matching shall require an **exact org match** and, per other axis, match when the resource value is a wildcard OR equals the user value | M |
| FR-4.4 | The system shall compute a member's asserted masks at request time as the Cartesian product of their assigned regions × departments × roles × groups (empty axis → `[0]`) — masks are **not** persisted per user | M |
| FR-4.5 | Folders shall carry compiled `view_permission_masks` and `contributor_permission_masks` derived from human-readable viewer/contributor permission configs | M |
| FR-4.6 | Folder listings shall be **permission-filtered at the database** via array overlap (`&&`); a folder with no view masks is visible to all members of the org | M |
| FR-4.7 | Documents shall inherit their folder's permission configs at creation; an org admin may override per-document; the document's own viewer perms take precedence over the folder's when deriving retrieval entitlements | M |
| FR-4.8 | RAG/search retrieval shall be filtered by the requesting user's access masks **before** any content reaches the LLM (masks are stored as `access_keys` on chunks and graph facts at ingest) | M |
| FR-4.9 | Org admins shall bypass mask filtering within their org (see all folders/documents, including unfiled documents) | M |
| FR-4.10 | Org admins shall manage dimensions (regions/departments/roles/groups), tags, document attributes, memberships, and folder permissions | M |
| FR-4.11 | The system shall prevent removal of the **last org admin** of an org (409) and prevent a user from removing their own membership unless they are a site admin | M |
| FR-4.12 | Permission changes should be auditable for compliance | S |

### FR-5: Folders

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-5.1 | The system shall organize documents in a hierarchical folder tree with unlimited nesting (self-referential `parent_id`) | M |
| FR-5.2 | Folder names shall be unique per `(org, parent)` and may not contain `.` (reserved for `dot_path`) | M |
| FR-5.3 | The system shall support moving/reparenting folders with **cycle prevention** and subtree `dot_path` rewrite (400 on a cycle) | M |
| FR-5.4 | The UI shall provide drag-and-drop folder moves and a virtualized folder tree | S |
| FR-5.5 | Deleting a folder shall be refused while it has child folders (409); documents in a deleted folder shall survive with `folder_id` set NULL | M |
| FR-5.6 | The UI shall provide a **Windows-Explorer-style two-pane resource browser** with Details/List/Small-icon/Large-icon views, sortable by Name/Type/Modified/Size (folders grouped first), all virtualized | S |

### FR-6: Document Management

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-6.1 | The system shall allow users with contribute access to create documents by pasting text (rich text → Markdown) | M |
| FR-6.2 | The system shall allow **binary file upload** (`POST /api/documents/upload`, multipart) storing the original in S3-compatible object storage under `{org_id}/{document_key}/{filename}` before commit | M |
| FR-6.3 | Upload shall accept `.pdf, .png, .jpg, .jpeg, .tif, .tiff, .bmp, .gif, .webp, .txt, .md, .docx, .doc, .zip`, enforced by an extension allowlist and a `MAX_FILE_SIZE_MB` cap (bounded streaming read) | M |
| FR-6.4 | For image/PDF uploads the user shall choose a text-extraction method: **`ocr`** (Tesseract, free) or **`ai`** (OpenAI vision, paid) | M |
| FR-6.5 | `.zip` uploads shall be server-expanded into one document per member (≤200 members, total-uncompressed cap, zip-bomb guards) and report created/skipped in a batch response | S |
| FR-6.6 | The system shall track document processing status through the states **PENDING → PROCESSING → SUCCESS / FAILED**, with structured `processing_details` on failure | M |
| FR-6.7 | The UI shall surface processing status via badges and shall poll (4–5 s) while any document is PENDING/PROCESSING | M |
| FR-6.8 | The system shall support metadata-only updates (title, folder, tags, permissions) that re-propagate derived tags/access-keys/title to existing vectors **without re-embedding** | M |
| FR-6.9 | The system shall support full content replacement (`PUT /api/documents/{id}/content`) that **re-chunks and re-embeds**, purging prior vectors first (re-ingest is not idempotent) | M |
| FR-6.10 | The system shall serve document content (`GET …/content`) typed as markdown/text/pdf/image/other, including presigned URLs for binary originals and an "Original" download | M |
| FR-6.11 | The system shall provide an in-explorer **Markdown editor** (CodeMirror split edit/preview) to create and edit `.md`/`.txt` documents, with a toolbar and a contextual Markdown-table editor | S |
| FR-6.12 | Deleting a document shall remove the Postgres row, best-effort delete the stored original, and best-effort remove its vectors/graph nodes | M |
| FR-6.13 | The system shall support flexible **tags** (org-scoped, unique per org), settable by any member | M |
| FR-6.14 | The system shall support org-defined **custom attributes** (freeform or picklist, optionally required) whose values are stored in the document's `metadata` JSONB | S |
| FR-6.15 | Unfiled documents (`folder_id IS NULL`) shall be visible to org admins; the create modal shall offer a folder picker to file documents | M |

### FR-7: Document Processing Pipeline

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-7.1 | The system shall process documents **asynchronously** via Celery workers (Redis broker), decoupled from the request that created them | M |
| FR-7.2 | The worker shall extract text per file type: `.txt/.md` decoded directly; `.docx` via **mammoth** (→ Markdown); `.doc` via **antiword** (legacy, worker-only, persisted as a `.extracted.txt` sidecar); PDFs/images via **Tesseract** or **OpenAI vision** per the chosen method | M |
| FR-7.3 | The system shall chunk text into ~**500-token** segments with **20-token** overlap, sentence-boundary aware (tokenizer `o200k_base`) | M |
| FR-7.4 | The system shall generate an **embedding** per chunk (`text-embedding-3-small`, 1536-dim) and store chunks in a per-tenant Qdrant collection with payload including text, summary, `chunk_order`, `document_key`, tags, and `access_keys` | M |
| FR-7.5 | The system shall generate **hierarchical summaries** (per-chunk → recursively grouped → document-level), producing a `summary_tree`; failures degrade gracefully to truncated/joined fallbacks | S |
| FR-7.6 | The system shall compute a document-level vector from the document summary embedding, or the centroid of chunk embeddings as fallback | S |
| FR-7.7 | When knowledge-graph extraction is enabled, the system shall extract `(subject, predicate, object)` triplets per chunk (parallel) and batch-insert them into Neo4j | S |
| FR-7.8 | Knowledge-graph extraction shall be toggleable per **org** (`Org.use_knowledge_graph`, default on) and overridable per **document** (`Document.use_knowledge_graph`) | S |
| FR-7.9 | For AI OCR the worker shall resolve the per-org OpenAI key via an internal API endpoint, falling back to the central key; the per-org key shall **never** be placed on the Celery broker | M |
| FR-7.10 | The worker shall report terminal status back to the API via the internal router; a callback failure shall not fail the ingest, but a missing internal key shall be logged loudly (docs would otherwise sit PENDING) | M |
| FR-7.11 | The worker shall retry Brain POSTs only on **5xx / 429 / network** errors (4xx are permanent → FAILED); PDF/image page count shall be capped at `MAX_OCR_PAGES` (default 100) | M |
| FR-7.12 | Empty/whitespace extraction shall terminate as `FAILED{stage: extraction, reason: empty_text}` rather than ingest an empty document | M |

### FR-8: Vector Search

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-8.1 | The system shall provide semantic search over document chunks via query embedding + Qdrant similarity (cosine), returning ranked hits with score and payload | M |
| FR-8.2 | Search shall filter by the user's `access_keys`, by required tags (AND), and by folder scope (`folder:<id>` tags, OR) | M |
| FR-8.3 | Search results shall be tenant-isolated by physically separate per-tenant collections plus `tenant_id` payload filtering | M |
| FR-8.4 | The UI shall provide a semantic search page ranking results by `% match` with highlighted snippets linking to the source document | S |

### FR-9: Knowledge Graph

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-9.1 | The system shall store extracted entities as Neo4j nodes labeled per tenant (`:Entity:Tenant_<id>`) and relationships as typed edges carrying `tags`, `access_keys`, and `document_key` | S |
| FR-9.2 | During hybrid RAG the system shall retrieve up to 10 relationship facts relevant to the query and include them in the LLM context as an explicit "Knowledge Graph Relationships" block | S |
| FR-9.3 | Graph retrieval shall respect RBAC (access-key intersection; empty access = public) and folder/tag scope | M |
| FR-9.4 | Graph facts shall be removed when their source document or tenant is deleted | M |

> **Note:** current graph retrieval is a keyword/substring match over the tenant's
> facts, not multi-hop Cypher traversal — see [§7](#7-known-limitations--deferred-work).

### FR-10: Conversational AI / RAG

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-10.1 | The system shall accept natural-language questions and answer them from retrieved context using an LLM (`gpt-5-mini`, `temperature=0.3`, `max_tokens≈1000`) | M |
| FR-10.2 | The system shall run **hybrid retrieval**: vector chunk search (default top-5) plus optional knowledge-graph facts, deduped to unique source documents | M |
| FR-10.3 | The LLM shall be instructed to answer **only** from provided context, with inline bracketed `[n]` citations and no outside knowledge | M |
| FR-10.4 | The system shall **stream** answers in real time over Server-Sent Events with event types `sources`, `graph`, `delta`, `done`, `error` | M |
| FR-10.5 | The UI shall stream over `fetch` (to carry `Authorization`/`X-Org-ID` headers), render tokens incrementally, and turn `[n]` markers into citation links to `/documents/{document_key}` | M |
| FR-10.6 | The UI shall abort the stream (cancelling downstream LLM cost) on new-chat, delete, unmount, or a new send | M |
| FR-10.7 | The system shall persist chat sessions per user (`chat_data` JSONB; history clamped to the last ~10 turns when building the prompt), owner-scoped and soft-deletable | M |
| FR-10.8 | The user shall be able to **scope** a query to selected folders and/or tags via a scope selector ("All documents" when none selected); scope shall reset on org change | S |
| FR-10.9 | Retrieval shall always be entitlement-filtered by the user's access masks in addition to any explicit scope | M |

### FR-11: Document Reader & Help

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-11.1 | The system shall provide a full-screen reader with side-by-side (summary ↔ full text, **scroll-synced**) and embedded (inline per-chunk summary) modes | S |
| FR-11.2 | The reader shall lazily load chunks a page at a time (page size 50) to scale to book-length documents | S |
| FR-11.3 | The system shall render a navigable document **summary tree** and an "indexed chunks" view showing how retrieval sees the document | S |
| FR-11.4 | The UI shall provide a **context-sensitive help panel** docked as a persistent right rail on desktop (≥1024 px) and an overlay drawer on mobile, resolving help topics by route | C |
| FR-11.5 | All rendered untrusted content (chunk text, Markdown) shall be sanitized (DOMPurify) before display | M |

### FR-12: Theming & UX

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-12.1 | The UI shall support **Light / Dark / Red Arch** themes, persisted in `localStorage` and applied pre-paint (no flash); first visit follows the OS preference | C |
| FR-12.2 | The UI shall surface success/error feedback via toasts and shall not 500 an already-committed document when the broker is unavailable at enqueue time | M |

---

## 4. Non-Functional Requirements

### NFR-1: Performance

| ID | Requirement | Target |
|----|-------------|--------|
| NFR-1.1 | List-endpoint response time | < 500 ms p95 |
| NFR-1.2 | Single-item GET response time | < 200 ms p95 |
| NFR-1.3 | Time to first token (streaming chat) | < 2 s |
| NFR-1.4 | Document ingestion throughput | > 10 docs/min/worker (text) |
| NFR-1.5 | Vector search latency (top-k=10) | < 100 ms |
| NFR-1.6 | Blocking clients (Qdrant/Neo4j/OpenAI) in Brain API shall run off the event loop (`asyncio.to_thread`) | — |

### NFR-2: Scalability

| ID | Requirement | Target |
|----|-------------|--------|
| NFR-2.1 | Concurrent users per org | 100+ |
| NFR-2.2 | Documents per org | 100,000+ |
| NFR-2.3 | Total chunks across tenants | 10M+ |
| NFR-2.4 | API services shall be stateless and horizontally scalable | Stateless design |
| NFR-2.5 | Workers shall scale independently (Redis broker, `acks_late`, prefetch=1) | Independent pool |
| NFR-2.6 | The UI file explorer/tree shall virtualize large lists (`react-window`) | — |

### NFR-3: Security

| ID | Requirement | Implementation |
|----|-------------|----------------|
| NFR-3.1 | All API endpoints authenticated (except health + setup status) | Clerk JWT / E2E header |
| NFR-3.2 | Tenant data isolation | PostgreSQL RLS (FORCE) + explicit `org_id` filtering |
| NFR-3.3 | No cross-tenant leakage in vector/graph search | Per-tenant collections + `access_keys` filtering + tenant labels |
| NFR-3.4 | Three distinct service secrets, none shared | Clerk JWT (user), `BRAIN_API_KEY` (API→Brain), `INTERNAL_API_KEY` (worker→API) |
| NFR-3.5 | Internal-key comparison constant-time | `hmac.compare_digest`; empty key → 503 (disabled, not open) |
| NFR-3.6 | Secrets provided via environment / secret manager, never hardcoded | 12-factor; required secrets enforced (`${VAR:?}`) in prod compose |
| NFR-3.7 | Input validation at all boundaries | Pydantic schemas; extension allowlist; UTF-8/zip-bomb guards |
| NFR-3.8 | Output sanitization in UI | DOMPurify on all untrusted HTML |
| NFR-3.9 | Security headers on UI responses | `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy` |
| NFR-3.10 | HTTPS/TLS in production | TLS termination at ingress |
| NFR-3.11 | Secret scanning in CI/pre-commit | gitleaks; `.env*` gitignored |

### NFR-4: Reliability

| ID | Requirement | Target |
|----|-------------|--------|
| NFR-4.1 | System availability | 99.5% uptime |
| NFR-4.2 | Data durability | PostgreSQL with backups |
| NFR-4.3 | Graceful degradation on Brain API failure | Best-effort cascades log and continue; Postgres delete not blocked |
| NFR-4.4 | Worker task retry with bounded backoff | 5xx/429/network only; max 3 retries; soft/hard time limits 1740/1800 s |
| NFR-4.5 | Enqueue failure shall not lose a committed document | Doc left PENDING for reconciliation; request still 201 |
| NFR-4.6 | Pooled-connection safety of RLS | Transaction-local `SET LOCAL ROLE` + `set_config`, auto-reset |

### NFR-5: Maintainability

| ID | Requirement | Implementation |
|----|-------------|----------------|
| NFR-5.1 | Test coverage (Python) | 80% minimum, CI-gated (`--cov-fail-under=80`) |
| NFR-5.2 | Type safety | mypy `strict` (Python); statically typed Go (migration target) |
| NFR-5.3 | Lint/format | ruff (line length 120) + ruff-format; ESLint 9 (UI) |
| NFR-5.4 | Structured logging | Single-line JSON with OTel `trace_id`/`span_id` correlation |
| NFR-5.5 | Health endpoints | `/healthz` on all services (`/readyz` partial — see §7) |
| NFR-5.6 | Test pyramid | pytest unit/integration; Vitest component; Playwright E2E |

### NFR-6: Deployment & Observability

| ID | Requirement | Implementation |
|----|-------------|----------------|
| NFR-6.1 | Container-based deployment | Docker images per service |
| NFR-6.2 | Local development | Docker Compose + hybrid `run-stack.sh` (host uvicorn/Next + dockerized infra) |
| NFR-6.3 | CI/CD | GitHub Actions (lint, type-check, python/go/ui tests, E2E, security) |
| NFR-6.4 | Database migrations | **Alembic** (Python, authoritative); golang-migrate (Go port) |
| NFR-6.5 | Configuration | Environment variables (12-factor) |
| NFR-6.6 | Tracing/metrics | OpenTelemetry (OTLP) + Prometheus instrumentator; Flower for Celery |

---

## 5. External Dependencies

| Dependency | Version | Purpose |
|------------|---------|---------|
| PostgreSQL | 18 (host port 5433) | Primary data store with RLS |
| Redis | 7.4 | Celery broker/result backend, setup-token store, caching |
| Qdrant | 1.12.4 | Vector similarity search |
| Neo4j | 5.25.1 (+ APOC) | Knowledge graph |
| MinIO / S3 | — | Object storage for original uploaded files |
| Clerk | SaaS | Identity provider (OIDC) |
| OpenAI API | — | Embeddings, chat completions, summaries, triplet extraction, AI OCR |
| Tesseract / poppler / antiword | — | Free OCR and legacy document text extraction (worker image) |

**Models:** chat/summary/triplets `gpt-5-mini`; embeddings `text-embedding-3-small` (1536-dim); AI OCR `gpt-4.1-mini` (vision).

---

## 6. Configuration Requirements

Required secrets (no safe default): `POSTGRES_PASSWORD`, `NEO4J_PASSWORD`, `OPENAI_API_KEY`,
`STORAGE_SECRET_KEY`, `API_SECRET_KEY`, `BRAIN_API_KEY`, `INTERNAL_API_KEY`,
`CLERK_JWT_ISSUER`, `CLERK_ALLOWED_AZP` (when Clerk enabled), `CLERK_SECRET_KEY`,
`NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`.

Key tunables: `MAX_FILE_SIZE_MB` (50), `MAX_OCR_PAGES` (100), `OPENAI_CHAT_MODEL`,
`OPENAI_EMBEDDING_MODEL`, `OPENAI_OCR_MODEL`, `BRAIN_MAX_TOKENS` (16000),
`API_RATE_LIMIT_PER_MINUTE` (config present — see §7), `OTEL_EXPORTER_OTLP_ENDPOINT`,
`LOG_LEVEL`. See [`.env.example`](../.env.example) for the full, grouped list.

---

## 7. Known Limitations & Deferred Work

These are truthfully-documented gaps between the aspirational feature set and the current code:

| Area | Limitation |
|------|------------|
| **Knowledge graph** | Retrieval is a case-insensitive **substring/keyword match** over all of a tenant's triples (then top-10), **not** multi-hop Cypher traversal. Relationship questions only work when a matching triple was extracted. Fetch-all-then-filter is also a scale concern (cf. NFR-2.3) |
| **`/readyz`** | Returns a static OK on the Python API — real dependency probes are deferred (REDARCH-12) |
| **Rate limiting** | `API_RATE_LIMIT_PER_MINUTE` is configured but no limiter middleware is currently mounted |
| **Contributor masks** | `contributor_permission_masks` are computed and stored but write authorization is currently enforced by role gates (org-admin/member), not a per-folder contributor-mask check |
| **Chat stream timeout** | The API→Brain streaming proxy has no request timeout (REDARCH-14) |
| **Re-ingest idempotency** | Brain ingest is not inherently idempotent (fresh UUIDs per run); the API compensates by purging vectors before re-ingest. Worker `acks_late` means a crash can re-run extraction (not exactly-once) |
| **Go stack** | The Go rewrite lacks chat/RAG, search proxy, setup wizard, site-admin, auth, and attributes surfaces, uses **asynq** (not interoperable with Celery), and is absent from prod/`run-stack.sh` |
| **Per-org OpenAI key** | Currently used only for worker AI OCR; the Brain API's embeddings/summaries/triplets/chat still use the central key |
| **Audit logging** | Permission-change audit (FR-4.12) is a Should-Have, not yet a durable trail |

---

## 8. Constraints

| ID | Constraint |
|----|------------|
| C-1 | The shipping backend is **Python** (FastAPI, async SQLAlchemy, Celery). A Go port is a directional goal but is **not** yet the authoritative stack |
| C-2 | Frontend is **Next.js 15** (App Router) with **React 18**, TypeScript, Tailwind v4; state via React Context + axios (no React Query) |
| C-3 | Must use the existing PostgreSQL schema with RLS and the `app_user`/`app_admin` role model |
| C-4 | Must authenticate exclusively via **Clerk** (Keycloak fully removed as of Slice 6) |
| C-5 | Must preserve the REST API contract the UI depends on |
| C-6 | The Python and Go task pipelines are **not** interoperable (Celery vs asynq) — run one full stack or the other, never mixed |
| C-7 | Object storage endpoints must not contain underscores (botocore rejects them) |
