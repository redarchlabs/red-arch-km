# Changelog

All notable changes to the Red Arch Knowledge Management Platform are documented in
this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added — Record-state platform (workflow read/write, live status boards, inline triggers)

- **`get_record` / `update_record` workflow actions** — read a record's live fields
  into run variables (`by_id` / `latest` / `first`, optional filters) and write
  multiple fields of a targeted record. Values/filters render both `{"$ref": ...}`
  envelopes and `{{ }}` templates.
- **`record_list` view element** — a read-only, optionally-polling table of an
  entity's records (a live "status board"), with an optional per-row workflow button.
  Polling pauses on hidden tabs and backs off on error.
- **Records list `order_by` / `order_dir`** query params; view viewer honours
  `?record_id=` (entity-bound prefill + run-workflow-against-record).
- **`run_inline_on_change`** per-workflow flag — fire an entity-change workflow
  synchronously in the mutating request (no beat-sweep delay), bounded by a hard
  time budget and dedup'd against the later sweep. Settable via `PATCH /workflows`
  and MCP `km2_update_workflow`. Migrations **024** (column) and **027** (partial index).

### Added — Knowledge engine, custom entities, workflow automation, intake forms, and tenant hardening (Slices 1, 5–7)

Marks the completion of **5 major slices** adding enterprise automation and knowledge-extraction capabilities:

#### Knowledge Engine (Slice 1): Neo4j-backed fact store
- **Reified-claim architecture** (`packages/brain_sdk/facts/`): tenant-scoped fact extraction and query via Neo4j
  triplet store (design: [`docs/KNOWLEDGE_ENGINE.md`](docs/KNOWLEDGE_ENGINE.md)).
- Extractors: `pipeline.py` (doc → triplets), `predicates.py` (filtering), `resolution.py` (dedup+merge).
- Tenant labels on all nodes/rels so queries are org-isolated.

#### Custom Entities (Slice 5): Dynamic, schema-driven records
- **Entity definitions + DDL**: org admins define entities (name, fields, relationships) via
  `POST /api/entity-definitions`; the API runs physical Postgres DDL to create `ce_<slug>` tables
  matching the schema on the fly. Catalog: `entity_definitions`, `entity_fields`, `entity_relationships`.
- **Entity records CRUD**: `GET/POST /api/entities/{slug}/records` with **keyset (cursor) pagination** for scalability.
  Cursor is an opaque, URL-safe token encoding `(created_at, id)` position; no OFFSET. Search via `?q=text`.
- **Identifier safety**: `services/identifiers.py` validates slugs for SQL injection & Postgres reserved words.
- **RLS enforcement**: record access scoped by org via RLS + explicit `org_id` filtering; any org member can CRUD.
- **Schema DDL service**: `services/schema_manager.py` safely runs CREATE TABLE with type coercion, FK validation.

#### Workflow Automation (Slices 5–6): Visual workflow engine with polling-based dispatch
- **Workflow authoring**: `POST /api/workflows/{id}` create, `/versions` save drafts, `/versions/{vid}/publish` go live.
  Versions are immutable once published (DB trigger). Workflows are tied to an entity definition.
- **Triggers**: `on_record_change` (create/update/delete events from entity operations); `on_form_submission` (intake-form
  link completed). Conditions evaluated against record snapshots.
- **Actions**: `update_record_field`, `create_record`, `send_email` (HTML template, recipient validated),
  `send_webhook` (allowlisted hosts), `send_form` (mint intake-form link).
- **Execution model**: 
  - **Outbox**: entity record changes written to `workflow_outbox` in the same transaction (at-least-once semantics).
  - **Dispatch**: Celery beat sweeps `workflow_outbox` for pending events (`/api/internal/workflows/dispatch-batch`);
    dispatcher claims with `FOR UPDATE SKIP LOCKED` (exactly-once per event); per-event RLS role downgrade so actions
    write as `app_user` scoped to the event's org.
  - **Timers**: `POST /api/internal/workflows/run-timers` resumes delayed runs and fires due scheduled workflows.
- **Manual run**: `POST /api/workflows/{id}/run` executes the published version against provided inputs, gated by
  `run_permission` (org_admin only by default; widened to any_member or roles/groups).
  - Security: **record ownership validated**; **side-effecting actions rejected on free-form data** (email/webhook
    require a real record).
- **Partitioned tables**: `workflow_runs` + `workflow_run_steps` are RANGE-partitioned by `created_at` with
  month boundaries; `workflow_ensure_partitions(months_ahead)` pre-creates upcoming partitions (idempotent PL/pgSQL fn).
  Default partition catches any off-schedule inserts.
- **Monitoring**: `GET /api/workflows/{id}/runs` + `GET /workflows/runs/{run_id}/steps` list executions and steps.

#### Intake Forms (Slice 6): Public, token-linked forms
- **Form definition**: `POST /api/forms` create, `PATCH` update, `DELETE` remove. Tied to an entity. Config (JSON) defines
  field mappings and behavior.
- **Link generation**: `POST /api/forms/{id}/links` with optional recipient email + expiry. Returns an opaque **SHA-256-hashed
  token** + a public URL (Mailpit for dev, real SMTP in production).
- **Public submission**: `GET /api/public/forms/{token}` render (resolves org from token on privileged session before any RLS),
  `POST /api/public/forms/{token}` submit (single-use, checks expiry + status). On success, **triggers a workflow** (if
  `on_form_submission` rule exists) or updates the target entity record directly.
- **Email delivery**: SMTP configurable; template HTML-escaped for safety.
- **Token security**: hashed for lookup (public path resolves org from hash, then RLS-scoped); single-use (status transitions
  `pending → submitted` or `expired`/`revoked`).

#### In-API Tool-Calling Agent (Slice 7 enhancement)
- `POST /api/agent/chat/stream` — org-admin-gated SSE endpoint running OpenAI function calling in-process.
- Tools: `create_entity`, `update_entity_field`, etc. (mutates entity definitions).
- Org's per-stored OpenAI key decrypted on each request; falls back to central key if not set.
- Short-lived sessions per tool call (no connection pool saturation); tool commits are atomic.

#### Tenant Isolation Hardening
- **RLS + explicit org_id filtering** (defense in depth): repositories filter every query by `org_id` AND RLS policies
  on the tenant role (`app_user`). Privileged (BYPASSRLS) sessions used only for cross-org operations (site-admin,
  setup token, token hash → org resolution).
- **Per-org OpenAI key encryption at rest** (migration 016): `Org.openai_api_key` stored encrypted with Fernet
  (symmetric, `ORG_ENCRYPTION_KEY` config); decrypted only for worker consumption via internal endpoint.
- **Workflow dispatcher exactly-once**: claim via `FOR UPDATE SKIP LOCKED` on resume; pg_advisory_lock on scheduled workflows.

#### Per-Document Permissions (Slice 7 prerequisite)
- **Columns added** (migration 015): `documents.view_permission_masks`, `documents.contributor_permission_masks`,
  `documents.viewer_permissions_config`, `documents.contributor_permissions_config`.
- **Precedence**: per-document config + masks override folder config if set; NULL = inherit from folder (existing behavior).
- Feed access-key resolution in `brain-api` so retrieval filters by document permissions.

#### New Migrations (011–016)
- **011**: `forms` + `form_links` tables (intake-form catalog + token history).
- **012**: `workflows.run_permission` JSONB column (mode + roles/groups allowlist).
- **013**: `workflow_outbox.source` column (trigger source: `record_change` or `form_submission`).
- **014**: `workflow_runs.delay_until` + resumption logic (scheduled/delayed runs).
- **015**: Per-document permission columns (precedence over folder).
- **016**: Encrypt existing `orgs.openai_api_key` rows at rest; add `ORG_ENCRYPTION_KEY` env var.

#### Security: OpenAI Key Encryption + HTTPS Validation
- Per-org OpenAI keys are encrypted at rest (Fernet symmetric); decrypted only when needed (worker consumption).
- Workflow webhooks: allowlist validation (SSRF guard); recipient email + form link expiry validated.
- Internal API key comparison: constant-time (`hmac.compare_digest`).

### Added — File upload + OCR ingestion, folder browsing, document feedback (v1 parity)

Closes a set of gaps between Knowledge Manager v1 and v2 where the ingest,
authoring, and organize surfaces had regressed to text-paste only.

- **Binary file upload + OCR/text extraction.** New `POST /api/documents/upload`
  (multipart) streams the original to MinIO/S3-compatible object storage
  (`Document.document_url` = object key; originals retained), then dispatches a
  new Celery `task_extract_and_ingest`. The worker extracts text via **Tesseract**
  (free) or **OpenAI gpt-4.1-mini vision** (paid), selectable per upload, then
  feeds the existing text ingest pipeline. Per-org OpenAI key (`Org.openai_api_key`)
  resolved via a new internal endpoint with fallback to the central key; the key
  never rides the Celery broker. Accepts PDF/PNG/JPG/TIFF/BMP/GIF/WEBP/TXT/MD with
  a size cap and extension allowlist. `delete_document` now also purges the stored
  original. New `minio` service in `docker-compose.infra.yml`.
- **Folder browsing.** New `folders/[id]` page lists a folder's documents;
  folder-tree names are now clickable. `GET /api/documents` accepts `?folder_id=`
  to scope to one folder (Python + Go handlers).
- **Chat context scoping.** The chat window can now be scoped to a folder; the
  `folder_ids` filter (previously accepted but ignored) is translated to
  `folder:<id>` tags and applied as an OR filter in the vector store
  (new `any_tags` / `MatchAny` support), on both the `/search/chat` and
  `/chat/sessions/{id}/ask` paths.

### Fixed — document visibility, status, and feedback

- **Unfiled documents were invisible to everyone (incl. admins).** A document
  created without a folder (`folder_id IS NULL`) never matched the `folder_id IN
  (...)` list filter, so pasted docs silently vanished. `list_documents` now
  surfaces unfiled docs to org admins (`include_unfiled`); the create modal also
  gained a folder picker so docs get filed. (Python aligned with the existing Go
  `isAdmin` behavior.)
- **Status badge never showed success/failure.** The worker writes
  `SUCCESS`/`FAILED` but the UI checked `COMPLETE`/`ERROR` and the model enum was
  dead code. `ProcessingStatus` reconciled to `PENDING/PROCESSING/SUCCESS/FAILED`
  and wired into the callback validator + UI badges; documents list now
  auto-refreshes while any doc is processing.
- **Create gave no feedback.** Added `sonner` toasts on success/error; a broker
  outage during enqueue no longer 500s an already-committed document.

### Security

- Internal API key comparison is now constant-time (`hmac.compare_digest`).

### Added — First-run setup wizard + global Site Admin console (Slice 7)

Replaces the Django-admin-era global administration workflow that was lost in
the platform rewrite:

- **First-run setup wizard.** On boot with no active site admin, the API
  generates a one-time setup token (SHA-256 hash in Redis, 24h TTL, single
  use, never overwritten while unclaimed) and prints it to its logs. A signed-in Clerk user
  claims global admin at `/setup` by pasting the token, then creates the
  first organization. Endpoints: `GET /api/setup/status` (public),
  `POST /api/setup/claim` (authenticated). Orgless users are auto-redirected
  into the funnel.
- **Site Admin console** at `/site-admin` (site admins only): Organizations
  CRUD (type-to-confirm delete), Users (search, promote/demote site admins,
  deactivate/reactivate), Memberships (org-centric add/remove/org-admin
  toggle across any org), and System status (PostgreSQL, Redis, Brain API,
  worker queue depth, API version) via `GET /api/admin/system`.
- **User deactivation.** New `user_profiles.is_active` column (migration
  004); deactivated accounts are rejected at auth time (403) on both the
  Clerk and E2E auth paths. Guards: self-demotion/self-deactivation → 400,
  removing the last active site admin → 409.
- **API.** New `/api/admin` router (`GET /users`, `PATCH /users/{id}`,
  `GET /users/{id}/memberships`, `GET /system`),
  `DELETE /api/memberships/{id}`, `BrainAPIClient.healthz()`.
- **UI.** Axios client now lets a per-request `X-Org-ID` header win over the
  ambient org from localStorage (required for cross-org administration).
- **Themes.** Selectable Light / Dark / **Red Arch** themes (palette + arch
  logo from the original v1 Knowledge Manager; see `ui/LOGO-LICENSE.md`).
  Theme picker in the header, persisted in localStorage, applied pre-paint
  (no flash), first visit follows the OS preference. Previously the UI was
  locked to the OS `prefers-color-scheme`.

### Fixed

- Org-switcher dropdown was clipped under the sidebar (right-anchored popover
  on a left-edge trigger inside an `overflow-hidden` column) — now anchored
  left and fully visible.
- Fresh sessions fired org-scoped requests without `X-Org-ID` (400s on
  Documents/Folders/Chat until an org was manually picked): the resolved
  initial org is now persisted to localStorage, which is what the API client
  reads.
- Clerk users whose session token carries no username/email claims (no JWT
  template configured) are now provisioned with sub-derived fallbacks instead
  of colliding on the empty-string unique constraint (500s for every user
  after the first).

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

### Changed — Authentication migrated from Keycloak to Clerk

End-user authentication moved from self-hosted **Keycloak** (OIDC) to **Clerk**
(cloud identity provider). The migration ran as a dual-verify coexistence window
(backends accepted a Keycloak *or* Clerk token, routed by the token `iss`) during
a soak period; **Slice 6 completes the cutover by removing Keycloak entirely**.
Clerk is now the sole identity provider.

- **RBAC/`access_mask` and multi-tenant RLS are unchanged** — identity is
  orthogonal to authorization; only the token verifier and IdP changed.
- **Service-to-service auth is unchanged** (`BRAIN_API_KEY` / `X-API-Key`,
  `INTERNAL_API_KEY` / `X-Internal-API-Key`).

#### Removed (Slice 6)

- `keycloak-js` dependency from the UI (`ui/package.json`).
- The Keycloak JWT verify path from the Go (`services/api-go`) and Python
  (`services/api`) backends, and the dual-verify (issuer-routing) branch — the
  backends now verify Clerk session tokens only.
- The `KEYCLOAK_URL` environment variable from `docker/docker-compose.go.yml`.
- **Environment variables removed** (delete these from any `.env`):
  `KEYCLOAK_URL`, `KEYCLOAK_REALM`, `KEYCLOAK_CLIENT_ID`, and the UI's
  `NEXT_PUBLIC_KEYCLOAK_URL` / `NEXT_PUBLIC_KEYCLOAK_REALM` /
  `NEXT_PUBLIC_KEYCLOAK_CLIENT_ID`.

#### Clerk configuration (required)

- Backend: `CLERK_JWT_ISSUER` (Clerk Frontend API URL — the token `iss`),
  `CLERK_ALLOWED_AZP` (comma-separated allowlist of UI origins; **mandatory** —
  Clerk tokens carry no `aud`, so `azp` is the security-critical origin check),
  `CLERK_SECRET_KEY`.
- UI: `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `NEXT_PUBLIC_CLERK_SIGN_IN_URL=/login`,
  `NEXT_PUBLIC_CLERK_SIGN_UP_URL=/sign-up`, and a Clerk JWT template
  (`NEXT_PUBLIC_CLERK_JWT_TEMPLATE=redarch-km`) emitting `email`,
  `email_verified`, and `username`.

#### Rollback note

Rollback during the soak window was a config flip (point the UI back at Keycloak;
backends still verified the Keycloak `iss`). **After Slice 6, rollback requires
restoring the Keycloak verify path, `keycloak-js`, the `KEYCLOAK_*` env, and the
Keycloak service** — this cutover was performed only after the soak was clean and
the human authorized it.

> Note: the `user_profiles.keycloak_sub` **column** is intentionally retained in
> the Python stack under its original name (it now stores the Clerk subject); the
> rename to `auth_subject` is a separate, deferred database migration.

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
