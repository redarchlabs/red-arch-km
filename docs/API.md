# API Reference

The KM2 platform exposes two HTTP services: the first-party **API** (`services/api`,
port 8000) that the Next.js UI and the versioned public surface run against, and the
knowledge **Brain API** (`services/brain_api`, port 8020) it calls for ingestion,
vector search, and RAG. This reference lists every route by area, the auth/permission
it requires, and links to request/response schemas — it is the map, not the full field
dump. Source of truth is the route decorators in `services/api/src/api/routers/` and
`services/brain_api/src/brain_api/routers/`; Pydantic bodies live in
`services/api/src/api/schemas/`.

## Table of Contents

- [Services and base paths](#services-and-base-paths)
- [Authentication](#authentication)
- [Conventions](#conventions)
- [Main API — first-party (Clerk session)](#main-api--first-party-clerk-session)
- [Enterprise API — `/api/v1` (org API key)](#enterprise-api--apiv1-org-api-key)
- [Brain API — service-to-service](#brain-api--service-to-service)
- [Error responses](#error-responses)
- [Rate limiting](#rate-limiting)
- [Pagination](#pagination)
- [Known gaps / TODO](#known-gaps--todo)

## Services and base paths

| Service | Dir | Port (dev) | Base path | Callers |
|---------|-----|-----------|-----------|---------|
| API | `services/api` | 8000 | `/api/*` (health at `/healthz`, `/readyz`) | UI, external systems (`/api/v1`), workers (`/api/internal`) |
| Brain API | `services/brain_api` | 8020 | `/healthz`, `/api/*`, `/api/v1/*` | The API service only (server-to-server) |

Routers are wired in `services/api/src/api/main.py` (`create_app`) and
`services/brain_api/src/brain_api/main.py`. All first-party routes are mounted under
`/api/...`; only the health probes are unprefixed. A Go rewrite of both services exists
under `services/api-go` and `services/brain-api-go` but the Python services above are
the authoritative ones today (see [ARCHITECTURE.md](ARCHITECTURE.md)).

## Authentication

Four independent credentials front the surfaces below. Full details — Clerk JWT
verification, the `redarch-km` template, RLS role-setting, and API-key hashing — live in
[AUTHENTICATION.md](AUTHENTICATION.md); this is the summary.

| Surface | Credential | Header | Enforced by |
|---------|-----------|--------|-------------|
| Main API `/api/*` | Clerk session JWT | `Authorization: Bearer <jwt>` | `get_current_user` in `api/auth/dependencies.py` |
| Main API tenant scope | Active org id | `X-Org-ID: <org_uuid>` | `get_org_id` in `api/dependencies.py` |
| Enterprise `/api/v1/*` | Org API key (`km2_…`) | `Authorization: Bearer km2_…` or `X-API-Key: km2_…` | `api/auth/api_key.py` |
| Internal callbacks `/api/internal/*` | Shared secret | `X-Internal-API-Key` | `require_internal_api_key` |
| Brain API | Shared secret (`BRAIN_API_KEY`) | `X-API-Key` | `brain_api/auth.py::require_api_key` |

Session-authenticated `/api/*` routes resolve the caller's role in the org named by
`X-Org-ID`. Three tiers gate them (see [RBAC.md](RBAC.md)):

- **member** — `require_org_access`: any user with a membership in the org (site admins
  get a synthetic admin membership).
- **org admin** — `require_org_admin`: membership with `is_org_admin` (or a site admin).
- **site admin** — `require_site_admin`: instance-wide `is_site_admin`; no `X-Org-ID`
  needed.

`GET /api/auth/me` returns `{sub, username, email}`. Deactivated profiles are rejected
at auth time (`403`).

## Conventions

- **Errors** use FastAPI's envelope: `{"detail": "..."}` (a string, or a structured
  object for the 409 in-flight-blocker case). See [Error responses](#error-responses).
- **Paginated lists** return `{items, total, page, page_size, pages}` (schema
  `PaginatedResponse` in `api/schemas/common.py`); query with `page`/`page_size`.
- **Entity records** use keyset cursors instead: `{items, next_cursor, limit}`, paged via
  an opaque `cursor` (no `OFFSET`).
- **Many list endpoints return a bare JSON array** (workflows, forms, views, reports,
  agents, work orders, promotions) rather than the paginated envelope.
- **Request/response shapes** are Pydantic models in `services/api/src/api/schemas/`.
  This doc shows a shape only where it aids understanding; otherwise it names the schema.

## Main API — first-party (Clerk session)

Base URL (dev): `http://localhost:8000`. Every route below is under `/api` except the two
health probes. "Auth" is the tier from [Authentication](#authentication); all
member/admin routes additionally require `X-Org-ID`.

### Health

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| GET | `/healthz` | Liveness — `{"status":"ok"}` | Public |
| GET | `/readyz` | Readiness — static `{"status":"ok"}` today (real probes tracked as REDARCH-12) | Public |

### Auth and setup

Routers `auth.py`, `setup.py`.

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| GET | `/api/auth/me` | Current user `{sub, username, email}` | Clerk (any user) |
| GET | `/api/setup/status` | First-run check — `{"needs_setup": bool}` | Public |
| POST | `/api/setup/claim` | Exchange the one-time setup token (from API logs) for site admin | Clerk (any user) |

### Organizations

Router `orgs.py`. Org membership is checked inside the handlers for read routes.

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| GET | `/api/orgs` | List orgs the caller can access (paginated) | Clerk (any user) |
| POST | `/api/orgs` | Create an org | Site admin |
| GET | `/api/orgs/{org_id}` | Org details | Clerk (member of that org) |
| PATCH | `/api/orgs/{org_id}` | Update org | Site admin |
| DELETE | `/api/orgs/{org_id}` | Delete org (cascades to all tenant data) | Site admin |

### Users and memberships

Routers `users.py`, `memberships.py`. Membership body: `MembershipRead`/create schema in
`api/schemas/membership.py`.

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| GET | `/api/users/me` | Current user + accessible orgs (`CurrentUserRead`) | Clerk (any user) |
| PATCH | `/api/users/me` | Update own profile (description only; name/email come from Clerk) | Clerk (any user) |
| GET | `/api/users` | List users in the current org (paginated) | Member |
| GET | `/api/memberships/by-user/{user_id}` | A user's membership in the current org (or `null`) | Org admin |
| POST | `/api/memberships` | Add a user to the org with regions/departments/roles/groups | Org admin |
| PATCH | `/api/memberships/{membership_id}` | Update a membership | Org admin |
| DELETE | `/api/memberships/{membership_id}` | Remove a user from the org (last-admin/self guards) | Org admin |

### Dimensions, tags, attributes

Routers `dimensions.py`, `tags.py`, `attributes.py`. Dimensions use a single generic
router where `{dimension}` ∈ `regions`, `departments`, `roles`, `groups`.

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| GET·POST | `/api/dimensions/{dimension}` | List / create a region, department, role, or group | Org admin |
| GET·PATCH·DELETE | `/api/dimensions/{dimension}/{id}` | Read / update / delete one | Org admin |
| GET·POST | `/api/tags` | List / create tags | Member |
| GET·PATCH·DELETE | `/api/tags/{tag_id}` | Read / update / delete a tag | Member |
| GET·POST | `/api/attributes` | List / create custom attribute definitions | Org admin |
| GET·PATCH·DELETE | `/api/attributes/{attribute_id}` | Read / update / delete an attribute | Org admin |

### Documents

Router `documents.py`. Reads and writes are member-gated with per-document permission
masks applied (see [document permissions](DATABASE.md) and the folder inheritance model).
Bodies: `api/schemas/document.py`.

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| GET | `/api/documents` | List documents the caller may view (paginated) | Member |
| POST | `/api/documents` | Create + ingest a text document | Member |
| POST | `/api/documents/upload` | Upload a file (multipart) and ingest it | Member |
| GET | `/api/documents/by-key/{document_key}` | Look up a document by its `document_key` | Member |
| GET | `/api/documents/{id}` | Document metadata + ingest status | Member |
| PATCH | `/api/documents/{id}` | Update metadata (title/description/tags/folder) | Member |
| DELETE | `/api/documents/{id}` | Delete (cascades to vector + graph stores) | Member |
| POST | `/api/documents/{id}/cancel` | Cancel an in-flight ingest job | Member |
| GET | `/api/documents/{id}/logs` | Ingest job logs (`JobLogsRead`) | Member |
| GET | `/api/documents/{id}/content` | Raw text/markdown content | Member |
| PUT | `/api/documents/{id}/content` | Replace markdown content (in-explorer editor) | Member |
| POST | `/api/documents/{id}/reprocess` | Re-run ingestion (e.g. a `FAILED` doc) | Member |
| GET | `/api/documents/{id}/chunks` | Indexed chunks | Member |
| GET | `/api/documents/{id}/summary` | Document summary tree | Member |

### Folders

Router `folders.py`. Read is member; create/update/delete are admin (they set the
permission config that documents inherit).

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| GET | `/api/folders` | List folders the caller may view (paginated) | Member |
| POST | `/api/folders` | Create a folder with viewer/contributor permission config | Org admin |
| GET | `/api/folders/{id}` | Folder details | Member |
| PATCH | `/api/folders/{id}` | Update folder (name, parent, permissions) | Org admin |
| DELETE | `/api/folders/{id}` | Delete folder | Org admin |

### Search and chat

Routers `search.py` (`/api/search`), `chat.py` (`/api/chat`). Search proxies the Brain
API with the caller's permission masks applied; the `/chat/*` streams return SSE. The
chat router only manages saved-session records — asking a question is a `/api/search/chat`
call. Bodies: `api/schemas/search.py`, `api/schemas/chat.py`.

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| POST | `/api/search` | Semantic (vector) search scoped to the caller's access | Member |
| POST | `/api/search/chat` | Hybrid RAG chat (answer + sources + graph) | Member |
| POST | `/api/search/chat/stream` | Streaming RAG chat (SSE) | Member |
| POST | `/api/search/chat/agent` | Agentic fact-engine chat (tool loop) | Member |
| POST | `/api/search/chat/agent/stream` | Streaming agentic chat (SSE) | Member |
| GET·POST | `/api/chat/sessions` | List / create chat-session records | Member |
| GET·PATCH·DELETE | `/api/chat/sessions/{id}` | Read / rename / delete a session | Member |

SSE event types across the streaming endpoints: `sources`, `graph`, `delta`, `done`,
`error`.

### Custom entities

Routers `entity_definitions.py` (`/api/entity-definitions`, admin — DDL) and
`entity_records.py` (`/api/entities`, member — data). Creating a definition builds a
physical `ce_<slug>` table. Record writes fire inline (on-change) workflows via the
outbox. Bodies: `api/schemas/custom_entity.py`, `api/schemas/aggregate.py`.

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| GET·POST | `/api/entity-definitions` | List / create entity definitions (paginated) | Org admin |
| GET·PATCH·DELETE | `/api/entity-definitions/{id}` | Read / update / delete a definition (drops its table) | Org admin |
| POST | `/api/entity-definitions/{id}/fields` | Add a field | Org admin |
| PATCH·DELETE | `/api/entity-definitions/{id}/fields/{field_id}` | Update / delete a field | Org admin |
| GET | `/api/entity-definitions/{id}/relationships` | Outgoing relationships | Org admin |
| GET | `/api/entity-definitions/{id}/incoming-relationships` | Incoming relationships | Org admin |
| POST | `/api/entity-definitions/{id}/relationships` | Create a relationship to another entity | Org admin |
| GET | `/api/entities/{slug}/records` | List records — keyset cursor + `q`/`filter`/`order_by` | Member |
| POST | `/api/entities/{slug}/records` | Create a record | Member |
| GET·PATCH·DELETE | `/api/entities/{slug}/records/{id}` | Read / update / delete a record | Member |
| POST | `/api/entities/{slug}/aggregate` | GROUP BY / metric query (`AggregateQuery` → `AggregateResult`) | Member |

`GET .../records` filters repeat as `?filter=<field>:<op>[:<value>]` (ops:
`eq ne gt gte lt lte in contains isnull`); `q` is a case-insensitive text search;
`order_by` + `order_dir` sort (the cursor carries the sort key). Aggregation metrics:
`count, count_distinct, sum, avg, min, max`; time buckets: `hour/day/week/month/quarter/year`.

### Reports and views

Routers `reports.py` (`/api/reports`), `views.py` (`/api/views`). Authoring is admin; list
/ read / run / render are member. A report is a saved `AggregateQuery` + a `viz` spec; a
view renders through the same `FormRenderRead` contract as forms
([FORMS_AND_VIEWS.md](FORMS_AND_VIEWS.md)). Bodies: `api/schemas/report.py`,
`api/schemas/view.py`.

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| GET | `/api/reports` | List saved reports | Member |
| POST | `/api/reports` | Create a report (query + viz validated against the entity) | Org admin |
| GET·PATCH·DELETE | `/api/reports/{id}` | Read / update / delete a report | member / admin / admin |
| POST | `/api/reports/{id}/run` | Run a saved report (optional filter/limit overrides) → `AggregateResult` | Member |
| POST | `/api/reports/run` | Run an unsaved aggregation (builder preview) | Member |
| GET | `/api/views` | List views | Member |
| POST | `/api/views` | Create a view | Org admin |
| GET·PATCH·DELETE | `/api/views/{id}` | Read / update / delete a view | member / admin / admin |
| GET | `/api/views/{id}/render` | Render a view (`?record_id=<uuid>` or `me` to bind the caller's own record) | Member |

### Forms

Router `forms.py`. Admin authoring + link management; member render/submit; and a
public, token-authenticated pair for external respondents (`public_router`, org resolved
from the token, rate-limited). Bodies: `api/schemas/form.py`, `api/schemas/form_elements.py`.

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| GET·POST | `/api/forms` | List / create forms | Org admin |
| GET·PATCH·DELETE | `/api/forms/{id}` | Read / update / delete a form | Org admin |
| GET | `/api/forms/{id}/links` | List generated one-time links | Org admin |
| POST | `/api/forms/{id}/links` | Generate a link (optionally emailed); raw token returned once | Org admin |
| POST | `/api/forms/{id}/links/{link_id}/revoke` | Revoke a link | Org admin |
| GET | `/api/forms/{id}/render` | Render a form for an authenticated member | Member |
| POST | `/api/forms/{id}/submit` | Submit as an authenticated member | Member |
| GET | `/api/public/forms/{token}` | Render a public form (`PublicFormRead`) | Public (token) |
| POST | `/api/public/forms/{token}` | Submit a public form → `204`; may trigger workflows | Public (token) |

### Workflows

Router `workflows.py` (`/api/workflows`). Authoring, versioning, connections, and inbound
endpoints are admin; running (gated per-workflow by `run_permission`) and completing a
user task are member. See [WORKFLOW_ENGINE.md](WORKFLOW_ENGINE.md). Bodies:
`api/schemas/workflow.py`, `api/schemas/workflow_definition.py`.

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| GET·POST | `/api/workflows` | List / create workflows | Org admin |
| GET·PATCH·DELETE | `/api/workflows/{id}` | Read / update / delete a workflow | Org admin |
| POST | `/api/workflows/{id}/run` | Run the published version for real (gated by `run_permission`) | Member |
| GET·POST | `/api/workflows/{id}/versions` | List / save draft versions | Org admin |
| POST | `/api/workflows/{id}/versions/{vid}/publish` | Publish a version (immutable) | Org admin |
| POST | `/api/workflows/{id}/versions/{vid}/test` | Dry-run test (no real side effects) | Org admin |
| GET | `/api/workflows/runs/recent` | Recent run activity across workflows | Org admin |
| GET | `/api/workflows/{id}/runs` | Runs for one workflow | Org admin |
| GET | `/api/workflows/runs/{run_id}/steps` | Per-step trace for a run | Org admin |
| POST | `/api/workflows/runs/{run_id}/complete-task` | Complete a parked user task | Member |
| GET | `/api/workflows/runs/{run_id}/stream` | Live run event stream (SSE) | Org admin |
| GET·POST | `/api/workflows/connections` | List / create outbound connections | Org admin |
| PATCH·DELETE | `/api/workflows/connections/{id}` | Update / delete a connection | Org admin |
| POST | `/api/workflows/connections/call` | Invoke a `call_connection` action (e.g. robot control) | Member |
| GET·POST | `/api/workflows/inbound-endpoints` | List / create inbound webhook endpoints (secret shown once) | Org admin |
| DELETE | `/api/workflows/inbound-endpoints/{id}` | Delete an inbound endpoint | Org admin |

The public receiver for inbound endpoints is `POST /api/inbound/{token}` (below).

### Inbound webhooks

Router `inbound.py`. Starts and runs inline the workflow bound to the token; a signed
endpoint additionally requires a valid `X-KM2-Signature` HMAC. See
[MCP_AND_INTEGRATIONS.md](MCP_AND_INTEGRATIONS.md).

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| POST | `/api/inbound/{token}` | Trigger the bound workflow with the JSON body as input | Public (token + optional HMAC) |

Errors: `401` on a missing/invalid signature (opaque), `404` on an unknown/disabled token.

### Agents (agent org)

Routers `agents.py`, `agent_console.py`, `agent_approvals.py`, `mcp_servers.py` (all
mounted under `/api/agents`) plus the legacy config assistant `agent.py` (`/api/agent`).
Config/authoring is admin; the interactive console + run history are member (the agent
acts only with the grants an admin gave it). See [AGENT_ORG.md](AGENT_ORG.md). Bodies:
`api/schemas/agent.py`, `api/schemas/agent_run.py`, `api/schemas/mcp_server.py`.

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| POST | `/api/agent/chat/stream` | Config assistant — tool-calling SSE stream (authoring tools admin-gated internally) | Member |
| GET | `/api/agents/providers` | LLM provider/model catalog + which have a usable key | Org admin |
| POST | `/api/agents/providers/credentials` | Store an org provider API key (write-only) | Org admin |
| DELETE | `/api/agents/providers/{provider}/credentials` | Remove the org's provider key | Org admin |
| GET·POST | `/api/agents` | List / create agents | Org admin |
| GET·PATCH·DELETE | `/api/agents/{agent_id}` | Read / update / delete an agent | Org admin |
| POST | `/api/agents/{agent_id}/console/stream` | Interactive agent run (SSE) | Member |
| GET | `/api/agents/{agent_id}/runs` | List an agent's runs | Member |
| GET | `/api/agents/runs/{run_id}` | Run detail | Member |
| GET | `/api/agents/runs/{run_id}/steps` | Run step trace | Member |
| GET | `/api/agents/approvals` | Pending tool-call approvals (authority "ask" tier) | Org admin |
| POST | `/api/agents/approvals/{id}/approve` · `/deny` | Resume / fail a parked run | Org admin |
| GET | `/api/agents/notifications` | Escalation/review inbox (`?unresolved_only`) | Org admin |
| GET | `/api/agents/notifications/unread-count` | Unread count | Org admin |
| POST | `/api/agents/notifications/{id}/{action}` | Mark `read` / `resolve` | Org admin |
| GET | `/api/agents/mcp-servers` | List registered MCP servers | Org admin |
| POST | `/api/agents/mcp-servers` | Register an MCP server (static-secret or OAuth) | Org admin |
| PATCH·DELETE | `/api/agents/mcp-servers/{id}` | Update / delete a server | Org admin |
| GET | `/api/agents/mcp-servers/presets` | Known-server presets for the create form | Org admin |
| POST | `/api/agents/mcp-servers/{id}/oauth/start` · `/oauth/disconnect` | Begin / clear the OAuth connection | Org admin |
| GET | `/api/agents/mcp-servers/oauth/callback` | OAuth redirect target (the `state` is the capability) | Public |
| POST | `/api/agents/mcp-servers/{id}/test` | Connect and list the server's tools | Org admin |

### Work orders

Router `work_orders.py` (`/api/work-orders`). Filing and reading are member; lifecycle
edits are admin. Filing may kick off the assigned supervisor agent. Bodies:
`api/schemas/work_order.py`.

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| GET·POST | `/api/work-orders` | List / file work orders | Member |
| GET | `/api/work-orders/{id}` | Detail (tasks, entries, progress) | Member |
| PATCH | `/api/work-orders/{id}/status` | Update status | Org admin |
| PATCH | `/api/work-orders/{id}/assignment` | Reassign to an agent | Org admin |
| PUT | `/api/work-orders/{id}/tasks` | Replace the task list | Org admin |

### Migration (import / export)

Router `migration.py` (`/api/migration`). Org-admin; operates on the caller's current
org. Services in `api/services/migration/`. Secrets are never exported. Related feature
doc: [CHANGE_MANAGEMENT.md](CHANGE_MANAGEMENT.md).

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| GET | `/api/migration/manifest` | Lightweight index of selectable objects (for the export picker) | Org admin |
| POST | `/api/migration/export` | Serialize the org to a downloadable JSON bundle (optional `selection`) | Org admin |
| POST | `/api/migration/diff` | Preview importing an uploaded bundle (`BundleDiff`, read-only) | Org admin |
| POST | `/api/migration/import` | Rebuild a bundle into the org (`?strategy=skip\|overwrite\|rename`, `?dry_run`) | Org admin |

### Promotions (change management)

Router `promotions.py` (`/api/promotions`). Org-admin release-promotion control plane
(targets → releases → diff → promote → rollback). A **local-org** promotion additionally
requires the caller to be an admin of the target org too (`_require_admin_of`). Full
model + lifecycle: [CHANGE_MANAGEMENT.md](CHANGE_MANAGEMENT.md).

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| GET·POST | `/api/promotions/targets` | List / create promotion targets (local-org or remote) | Org admin |
| POST | `/api/promotions/targets/{id}/test` | Probe a target's reachability + config access | Org admin |
| DELETE | `/api/promotions/targets/{id}` | Delete a target | Org admin |
| GET·POST | `/api/promotions/releases` | List / create releases (`selection` snapshot) | Org admin |
| GET | `/api/promotions/releases/{id}` | Release detail (items, approvals, promotions) | Org admin |
| POST | `/api/promotions/releases/{id}/submit` | Submit for approval | Org admin |
| POST | `/api/promotions/releases/{id}/approve` · `/reject` | Record an approval decision | Org admin |
| POST | `/api/promotions/releases/{id}/diff` | Preview a promotion against a target (`BundleDiff`) | Org admin |
| POST | `/api/promotions/releases/{id}/promote` | Promote to a target (409 with blockers if in-flight runs) | Org admin |
| GET | `/api/promotions` | List promotions (optional `?release_id`) | Org admin |
| POST | `/api/promotions/{promotion_id}/rollback` | Roll a promotion back using its reverse snapshot | Org admin |

### API keys

Router `api_keys.py` (`/api/api-keys`). The org-admin surface that mints and revokes the
`km2_…` keys used by the enterprise API. Plaintext is returned exactly once, from `POST /`.
Bodies: `api/schemas/api_key.py`.

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| GET | `/api/api-keys/scopes` | Scope catalog (for the create form) | Org admin |
| GET | `/api/api-keys` | List keys (metadata only) | Org admin |
| POST | `/api/api-keys` | Mint a key — response `key` shown once | Org admin |
| DELETE | `/api/api-keys/{id}` | Revoke a key (idempotent) | Org admin |

### Admin (site admin)

Router `admin.py` (`/api/admin`). Every route requires `is_site_admin`; no `X-Org-ID`.
Bodies: `api/schemas/admin.py`.

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/admin/users` | All users across the instance (`?page`, `?page_size`, `?q`) |
| PATCH | `/api/admin/users/{profile_id}` | Set `is_site_admin` / `is_active` (last-admin + self guards) |
| GET | `/api/admin/users/{profile_id}/memberships` | One user's memberships across every org |
| GET | `/api/admin/system` | Platform health (database, redis, brain_api, worker queue) |
| GET | `/api/admin/celery` | Celery worker/queue status |
| POST | `/api/admin/jobs/{document_id}/cancel` | Cancel a stuck ingest job |
| GET | `/api/admin/jobs/{document_id}/logs` | Ingest job logs |
| GET | `/api/admin/emails` | Sent-email list (Mailpit proxy, dev/staging) |
| GET | `/api/admin/emails/{message_id}` | Sent-email detail |
| GET | `/api/admin/deployments` | Change-management deployment log rows |

### Internal (service-to-service)

Router `internal.py` (`/api/internal`). Not for end users — gated by `X-Internal-API-Key`.
Worker callbacks and the Celery-beat sweep tasks. Bodies are inline models in the router.

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/internal/documents/{document_id}/status` | Worker reports document processing status |
| GET | `/api/internal/orgs/{org_id}/openai-key` | Per-org OpenAI key (decrypted) for the worker |
| POST | `/api/internal/workflows/dispatch-batch` | Process a batch of workflow-outbox events |
| POST | `/api/internal/workflows/run-timers` | Resume due delayed runs + fire scheduled workflows |
| POST | `/api/internal/workflows/advance-tokens` | Drive the BPMN token engine (parked/active tokens) |
| POST | `/api/internal/agents/advance-runs` | Claim + drive queued agent runs |
| POST | `/api/internal/agents/run-schedules` | Fire due cron-triggered agent schedules |
| POST | `/api/internal/workflows/maintain-partitions` | Pre-create upcoming month partitions (`?months_ahead`) |

## Enterprise API — `/api/v1` (org API key)

The versioned public surface (`services/api/src/api/routers/v1/`) is authenticated by an
org **API key**, not a Clerk session — no `X-Org-ID` (the key resolves to its org) and no
per-user permission masks (a key has org-wide visibility). Each endpoint declares the one
scope it needs; `has_scope` in `api/services/api_key_scopes.py` decides access. `*` and
`<domain>:*` wildcards are honored **except** `config:write`, which must be granted
explicitly. Missing scope → `403`; unknown/revoked/expired/missing key → opaque `401`.

Interactive docs: `GET /api/v1/docs` + `GET /api/v1/openapi.json` (gated by
`API_DOCS_ENABLED`, default on). Rate limits: see [Rate limiting](#rate-limiting).

### Scopes

Catalog (`API_SCOPES`) — only scopes with a live `/api/v1` endpoint are listed:

| Scope | Grants |
|-------|--------|
| `entities:read` | List/read entity definitions (schema) |
| `records:read` | List/read entity records + aggregations |
| `records:write` | Create/update/delete entity records |
| `reports:read` | List/read saved reports |
| `reports:run` | Execute reports + ad-hoc aggregations |
| `workflows:read` | List workflows, inspect runs |
| `workflows:run` | Trigger runs — **HIGH**: runs ANY workflow, bypassing per-user run permission |
| `search:read` | Semantic search + RAG chat |
| `knowledge:read` | List/read folders + documents + chunks/summary |
| `agents:read` | List agents, inspect agent runs |
| `agents:run` | Trigger an agent run — **HIGH**: agent acts with its configured grants |
| `work_orders:read` | List/read work orders |
| `work_orders:write` | File/update work orders |
| `config:read` | Read config info (verify a promotion connection) |
| `config:write` | Receive + apply config promotions — **VERY HIGH**, never granted by a wildcard |

### Endpoints

| Method | Path | Purpose | Scope |
|--------|------|---------|-------|
| GET | `/api/v1/entities` | List entity definitions (+ fields, paginated) | `entities:read` |
| GET | `/api/v1/entities/{slug}` | One entity definition | `entities:read` |
| GET | `/api/v1/entities/{slug}/records` | List records (keyset cursor, `q`/`filter`/`order_by`; `@me` rejected) | `records:read` |
| GET | `/api/v1/entities/{slug}/records/{id}` | One record | `records:read` |
| POST | `/api/v1/entities/{slug}/aggregate` | GROUP BY / metric aggregation | `records:read` |
| POST | `/api/v1/entities/{slug}/records` | Create a record (fires inline workflows) | `records:write` |
| PATCH | `/api/v1/entities/{slug}/records/{id}` | Update a record | `records:write` |
| DELETE | `/api/v1/entities/{slug}/records/{id}` | Delete a record | `records:write` |
| GET | `/api/v1/reports` | List saved reports | `reports:read` |
| GET | `/api/v1/reports/{id}` | One report definition | `reports:read` |
| POST | `/api/v1/reports/{id}/run` | Run a saved report (optional overrides) | `reports:run` |
| POST | `/api/v1/reports/run` | Run an ad-hoc aggregation | `reports:run` |
| GET | `/api/v1/workflows` | List workflows | `workflows:read` |
| GET | `/api/v1/workflows/{id}` | One workflow | `workflows:read` |
| POST | `/api/v1/workflows/{id}/run` | Run the published version for real | `workflows:run` |
| GET | `/api/v1/workflows/{id}/runs` | Recent runs | `workflows:read` |
| GET | `/api/v1/workflows/runs/{run_id}/steps` | Run step trace | `workflows:read` |
| POST | `/api/v1/search` | Semantic (vector) search | `search:read` |
| POST | `/api/v1/search/chat` | Hybrid RAG chat | `search:read` |
| GET | `/api/v1/knowledge/folders` | List folders | `knowledge:read` |
| GET | `/api/v1/knowledge/documents` | List documents (`?folder_id`, paginated) | `knowledge:read` |
| GET | `/api/v1/knowledge/documents/{id}` | Document metadata + status | `knowledge:read` |
| GET | `/api/v1/knowledge/documents/{id}/chunks` | Extracted chunks (paged) | `knowledge:read` |
| GET | `/api/v1/knowledge/documents/{id}/summary` | Document summary tree | `knowledge:read` |
| GET | `/api/v1/agents` | List agents | `agents:read` |
| POST | `/api/v1/agents/{agent_id}/run` | Queue an agent run (202; poll the run) | `agents:run` |
| GET | `/api/v1/agents/runs/{run_id}` | Agent run detail | `agents:read` |
| GET | `/api/v1/work-orders` | List work orders | `work_orders:read` |
| POST | `/api/v1/work-orders` | File a work order | `work_orders:write` |
| GET | `/api/v1/config/ping` | Authenticated probe — reports bundle format version | `config:read` |
| POST | `/api/v1/config/promotions` | Receive + apply a pushed config bundle (`?dry_run`, 409 on in-flight runs) | `config:write` |

`config:write` is the remote receiver for cross-instance promotions and runs on the
DDL-capable (still RLS-scoped) owner session; see [CHANGE_MANAGEMENT.md](CHANGE_MANAGEMENT.md).

## Brain API — service-to-service

Base URL (dev): `http://localhost:8020`. Called only by the API service (via
`api/services/brain_client.py`). Every route except `/healthz` requires `X-API-Key:
${BRAIN_API_KEY}`. Brain API **trusts** the caller-supplied `tenant_id` and `access_keys`
— the API service scopes them to the authenticated end user before calling; the key must
never reach browsers. Prefixes: `ingest`/`search` under `/api`, `rag`/`agent` under
`/api/v1`. See [KNOWLEDGE_ENGINE.md](KNOWLEDGE_ENGINE.md).

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/healthz` | Liveness — `{"status":"ok"}` |
| POST | `/api/ingest-document` | Ingest a document (chunk + embed + graph); returns `202`, processed in background |
| GET | `/api/ingest-status/{tenant_id}/{document_key}` | Poll ingest job state (`running`/`done`/`failed`/`unknown`) |
| POST | `/api/remove-document` | Remove a document from the vector + graph stores |
| POST | `/api/update-document-metadata` | Update stored title/tags/access keys |
| POST | `/api/init-tenant` | Initialize a tenant's collections |
| POST | `/api/remove-tenant` | Delete all of a tenant's data |
| GET | `/api/documents/{tenant_id}/{document_key}/chunks` | Fetch a document's chunks |
| GET | `/api/documents/{tenant_id}/{document_key}/summary` | Fetch a document's summary tree |
| POST | `/api/vector-search` | Semantic search (`hits` + `total`) |
| POST | `/api/vector-chat` | RAG chat with history |
| POST | `/api/v1/ask` | Non-streaming RAG query |
| POST | `/api/v1/ask/stream` | Streaming RAG query (SSE: `sources`/`graph`/`delta`/`done`/`error`) |
| POST | `/api/v1/agent/ask` | Agentic (fact-engine) query with citations |
| POST | `/api/v1/agent/ask/stream` | Streaming agentic query (SSE trace) |
| POST | `/api/v1/agent/digest/rebuild` | Rebuild a tenant's fact digest |
| GET | `/api/v1/agent/gaps` | List knowledge gaps |
| POST | `/api/v1/agent/gaps/status` | Update a gap's status |
| POST | `/api/v1/agent/gaps/re-extract` | Re-run extraction for a gap |

## Error responses

Errors use FastAPI's `{"detail": ...}` envelope. `detail` is usually a string; the
in-flight-blocker `409` (promotion / config apply) returns a structured object
`{"message": ..., "blockers": [...]}`.

| Code | Meaning |
|------|---------|
| 400 | Bad request / validation-domain error |
| 401 | Missing or invalid auth (opaque for API keys and signed webhooks) |
| 403 | Authenticated but lacks the required role/scope |
| 404 | Resource not found (or RLS-filtered) |
| 409 | Conflict — duplicate name, last-admin guard, or in-flight-run block |
| 413 | Upload too large (e.g. migration bundle over the size cap) |
| 422 | Request-body validation error (Pydantic) |
| 500 | Unhandled server error |
| 502 | Upstream service error (e.g. Brain API or an MCP server unreachable) |
| 503 | Feature disabled (e.g. internal API with no key configured) |

## Rate limiting

There is **no global per-user limiter** on the main API. Two limiters exist:

| Surface | Limit (env) | Default | Headers |
|---------|-------------|---------|---------|
| Public form render/submit (`/api/public/forms/{token}`) | `API_RATE_LIMIT_PER_MINUTE` (`rate_limit_per_minute`) | 60/min | — |
| Enterprise `/api/v1/*` per key | `API_KEY_RATE_LIMIT_PER_MINUTE` (`api_rate_limit_per_minute`) | 600/min | `X-RateLimit-Limit`, `X-RateLimit-Remaining`; `Retry-After` on `429` |
| Enterprise pre-auth per client IP | `API_IP_RATE_LIMIT_PER_MINUTE` (`api_ip_rate_limit_per_minute`) | 1200/min | `Retry-After` on `429` |

The `/api/v1` limiters use Redis (`api/services/api_rate_limit.py`) and **fail open** if
Redis is down. There is no `X-RateLimit-Reset` header.

## Pagination

Two models coexist:

- **Offset pages** — `PaginationParams` (`api/schemas/common.py`): query `?page` (default
  1) and `?page_size` (default 20, max 200). Response envelope
  `{items, total, page, page_size, pages}` (`PaginatedResponse[T]`). Used by users,
  documents, folders, entity definitions, tags, attributes, dimensions, admin, and the
  `/api/v1` entities/knowledge lists.
- **Keyset cursors** — entity records (`/api/entities/.../records` and the `/api/v1`
  equivalent): query `?cursor` + `?limit` (default 50, max 200), response
  `{items, next_cursor, limit}`; `next_cursor` is `null` at the end.

## Known gaps / TODO

- `AUTHENTICATION.md`, `CHANGE_MANAGEMENT.md`, and `MCP_AND_INTEGRATIONS.md` are linked
  above as the owners of auth, release-promotion, and integration detail; they are being
  authored alongside this rewrite and may not all be present yet.
- `GET /readyz` returns a static `ok` (real DB/Redis/Brain probes deferred — REDARCH-12).
- Enterprise `knowledge:*` is read-only in this release; `config:write` is the only
  write-capable config scope and is deliberately non-wildcardable.

> Reviewed 2026-07-16 against Alembic migration 039. Source of truth is the code; if this doc disagrees with the code, the code wins.
