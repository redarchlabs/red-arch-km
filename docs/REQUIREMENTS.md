# Red Arch Knowledge Manager — Requirements

Product requirements (functional + non-functional) for the Red Arch Knowledge
Management Platform v2 (KM2). For engineers and technical evaluators verifying what
the platform must do and how it does it. Requirements are grounded in the shipping
code; anything not yet built is labelled **roadmap**, not stated as fact.

> **Status:** Reflects the **Python** implementation that ships today (`services/api`,
> `services/brain_api`, `services/worker`, `ui/`), verified against the codebase at
> Alembic migration **039** (`039_entity_access_control`). A parallel Go rewrite
> (`services/*-go`) is in progress and **not yet authoritative** — see
> [§9 Constraints](#9-constraints) and [ARCHITECTURE.md](ARCHITECTURE.md).

KM2 is a multi-tenant, AI-powered enterprise knowledge and operations platform. It
combines Retrieval-Augmented Generation (RAG), vector search, and a knowledge graph
with a low-code application layer — custom entities, forms/views/dashboards, a
workflow engine, reporting, an AI agent organization, and an enterprise API — all
governed by fine-grained role-based access control (RBAC) and PostgreSQL Row-Level
Security (RLS).

---

## Table of Contents

1. [Actors & Roles](#1-actors--roles)
2. [Glossary](#2-glossary)
3. [Functional Requirements](#3-functional-requirements)
4. [Non-Functional Requirements](#4-non-functional-requirements)
5. [External Dependencies](#5-external-dependencies)
6. [Configuration Requirements](#6-configuration-requirements)
7. [Reference Applications](#7-reference-applications)
8. [Known Limitations & Deferred Work](#8-known-limitations--deferred-work)
9. [Constraints](#9-constraints)
10. [Out of Scope](#10-out-of-scope)
11. [Related Documentation](#11-related-documentation)

---

## 1. Actors & Roles

| Actor | Description | Privilege source |
|-------|-------------|------------------|
| **Anonymous visitor** | Unauthenticated browser; may only reach `/login`, `/sign-up`, the public setup-status probe, and public form-token pages | — |
| **Member** | Authenticated user with a membership in ≥1 org; access gated by 32-bit permission masks | `user_org_memberships` + dimension assignments |
| **Org Admin** | Full control within a single org (folders, dimensions, attributes, tags, memberships, entities, forms/views, workflows, reports, agents, API keys, releases) | `user_org_memberships.is_org_admin = true` |
| **Site Admin** | Instance-wide superuser; manages all orgs and users; receives a synthetic org-admin membership in every org | `user_profiles.is_site_admin = true` |
| **AI Agent** | An in-org autonomous actor (coordinator/advisory/operator) that plans, delegates, and acts on the org's data under the authority engine and human approval | `agents` catalog (migration 030) + grants |
| **API-key client** | An external caller acting with org-wide data visibility over the `/api/v1` surface, gated by key scopes | `api_keys` (migration 028), `km2_…` bearer |
| **Worker (service)** | Celery background process; calls the API's internal router with a shared secret | `X-Internal-API-Key` |
| **Brain API (service)** | AI/ML service; called by the API with a shared secret | `X-API-Key` (`BRAIN_API_KEY`) |
| **Identity Provider** | Clerk (OIDC/OAuth2); issues session JWTs | External SaaS |

Privilege ordering: **Site Admin ⊃ Org Admin ⊃ Member ⊃ Anonymous**. AI Agents and
API-key clients act within a single org and are always subject to server-side RLS,
scope, and authority checks.

---

## 2. Glossary

| Term | Meaning |
|------|---------|
| **Access mask** | A 32-bit integer encoding `[org:11][region:5][role:5][group:7][dept:4]`, matching a user's entitlements against a resource's permissions |
| **Wildcard** | The maximum value of a mask field (e.g. region=31), meaning "any value on this axis" — org is **never** a wildcard |
| **Permission config** | Human-readable JSON (`[{"region":"North","role":"Manager"}, …]`) that compiles to access masks; entries are OR'd, dimensions within an entry AND'd |
| **Dimension** | One of four org-scoped classification axes: **regions, departments, roles, groups** |
| **Chunk** | A ~500-token semantic segment of a document, embedded and stored in Qdrant |
| **Triplet / claim** | A `(subject, predicate, object)` fact extracted from a chunk and stored in Neo4j (tenant-labelled) |
| **`document_key`** | A per-org-unique UUID linking a Postgres document row to its vectors/graph nodes |
| **Custom entity** | An org-defined record type; its schema is materialized as a physical `ce_<slug>` Postgres table |
| **Form / View** | A saved v2 element tree (`{version:2, elements:[]}`); forms capture data, views render screens/dashboards; both use one `FormRenderer` |
| **Workflow** | An automation graph (BPMN 2.0.2 token engine or legacy walker) tied to an entity or run manually |
| **Report** | A saved aggregate query coupled to a chart/KPI/table visualization |
| **Agent org** | A tenant's tree of AI agents governed by a `deny > ask > allow` authority engine |
| **Release / promotion** | A frozen copy of an org's configuration moved through review and promoted to another environment |
| **`lineage_id`** | Durable cross-environment identity on config objects (migration 037), so a promoted copy stays linked to its source |
| **Tenant** | Synonymous with **org**; the isolation boundary for all data |

---

## 3. Functional Requirements

Priority key: **M** = Must Have, **S** = Should Have, **C** = Could Have.
Roadmap items are flagged inline and consolidated in [§8](#8-known-limitations--deferred-work).

### FR-1: Authentication & Identity

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-1.1 | The system shall authenticate end users via **Clerk** session JWTs (RS256, JWKS fetched from the issuer and cached ~300 s) | M |
| FR-1.2 | The system shall pin the token issuer to `CLERK_JWT_ISSUER` and re-verify it independently of any unverified header hint | M |
| FR-1.3 | Because Clerk session tokens carry no `aud`, the system shall enforce a **default-deny `azp` allowlist** (`CLERK_ALLOWED_AZP`); a token whose `azp` is missing or not allowlisted shall be rejected | M |
| FR-1.4 | The system shall fail startup if Clerk is configured without an `azp` allowlist | M |
| FR-1.5 | The system shall auto-provision a `user_profile` on first authenticated request (keyed on the token `sub`) and resync `username`/`email` from claims each subsequent request | M |
| FR-1.6 | The system shall provision sub-derived fallbacks (`username=sub`, `email={sub}@placeholder.invalid`) when a token carries no username/email claim | M |
| FR-1.7 | The system shall reject authentication for a deactivated account (`is_active=false`) with HTTP 403 on both the Clerk and E2E auth paths | M |
| FR-1.8 | The system shall support an **E2E test auth bypass** (`X-Test-User` + `X-Test-Secret`) gated by `e2e_test_mode`, for automated tests only — never enabled in production | M |
| FR-1.9 | All main-API endpoints except health, `GET /api/setup/status`, and the public form-token routes shall require a valid session | M |
| FR-1.10 | The UI shall gate routes both server-side (Clerk middleware `auth.protect()`) and client-side (redirect to `/login`) | M |
| FR-1.11 | The versioned `/api/v1` surface shall authenticate via **org API keys** (`Authorization: Bearer km2_…` or `X-API-Key`), independent of Clerk | M |

### FR-2: Multi-Tenancy & Organizations

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-2.1 | The system shall isolate all tenant data by org, enforced by **PostgreSQL Row-Level Security (RLS)** on tenant tables and by explicit `org_id` filtering in every repository (defense in depth) | M |
| FR-2.2 | The system shall resolve the active tenant from a client-supplied `X-Org-ID` header (validated as a UUID; 400 on missing/invalid) and bind it to the RLS session GUC `app.current_tenant_id` per request | M |
| FR-2.3 | RLS policies shall **fail closed**: an unset/empty tenant GUC shall normalize to NULL and return zero rows / block writes (not raise a 500) | M |
| FR-2.4 | The system shall allow a user to belong to multiple orgs and switch between them without re-authenticating (client changes `X-Org-ID`; membership re-verified each request) | M |
| FR-2.5 | The system shall allow site admins to create, read, update, and delete organizations | M |
| FR-2.6 | On org creation the system shall assign the next sequential `permission_number` (under a row lock) and best-effort initialize the tenant's Brain resources (Qdrant collections, Neo4j schema) | M |
| FR-2.7 | On org deletion the system shall cascade-delete all tenant rows in Postgres and best-effort purge the tenant's Qdrant collections and Neo4j nodes; Brain cleanup failures shall not block the Postgres delete | M |
| FR-2.8 | Each org shall support optional per-org **provider credentials** (OpenAI/Anthropic/Gemini keys, encrypted at rest with Fernet, migrations 016/029), a `use_knowledge_graph` default, and an `agent_autonomy` posture | S |
| FR-2.9 | The UI shall present an org switcher (static label for one org, dropdown for many) and persist the selection in `localStorage` | S |

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
| FR-3.9 | Site admins shall have a **Sent Emails** tab (Mailpit proxy, dev/staging only) and a **Deployments** tab surfacing promotion/deployment logs | C |
| FR-3.10 | The system shall prevent self-demotion, self-deactivation, and removal/deactivation of the **last active site admin** (guarded with an advisory lock; 400/409) | M |

See [SITE_ADMIN.md](SITE_ADMIN.md).

### FR-4: Access Control (RBAC & Entity/Field Policies)

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-4.1 | The system shall support multi-dimensional permissions across four org-scoped axes: **regions, roles, groups, departments** | M |
| FR-4.2 | The system shall encode entitlements as **32-bit access masks** with layout `[org:11][region:5][role:5][group:7][dept:4]` | M |
| FR-4.3 | Mask matching shall require an **exact org match** and, per other axis, match when the resource value is a wildcard OR equals the user value | M |
| FR-4.4 | The system shall compute a member's asserted masks at request time as the Cartesian product of their assigned regions × departments × roles × groups (empty axis → `[0]`) — masks are **not** persisted per user | M |
| FR-4.5 | Folders shall carry compiled `view_permission_masks` and `contributor_permission_masks` derived from human-readable viewer/contributor configs | M |
| FR-4.6 | Folder listings shall be **permission-filtered at the database** via array overlap (`&&`); a folder with no view masks is visible to all members of the org | M |
| FR-4.7 | Documents shall inherit their folder's configs at creation; an org admin may override per-document; the document's own viewer perms take precedence when deriving retrieval entitlements | M |
| FR-4.8 | RAG/search retrieval shall be filtered by the requesting user's access masks **before** any content reaches the LLM (masks stored as `access_keys` on chunks and graph facts at ingest) | M |
| FR-4.9 | Org admins shall bypass mask filtering within their org (see all folders/documents, including unfiled documents) | M |
| FR-4.10 | Org admins shall manage dimensions, tags, document attributes, memberships, and folder permissions | M |
| FR-4.11 | The system shall prevent removal of the **last org admin** of an org (409) and prevent a user removing their own membership unless a site admin | M |
| FR-4.12 | The system shall support **per-entity write access** (`entity_definitions.write_access` = `member` \| `workflow_only`) and **per-field read access** (`entity_fields.read_access` = `member` \| `server_only`), migration 039, so a record surface can be made tamper-proof (e.g. a quiz answer key or certification) | M |
| FR-4.13 | Under `workflow_only`/`server_only`, direct member writes shall 403 and `server_only` field values shall be stripped from the record API and disallowed in filter/sort/group; only the **workflow engine and org admins** (privileged sessions) bypass | M |
| FR-4.14 | Both entity/field policies shall default to the pre-existing fully-open behaviour, leaving existing entities unaffected until an admin opts in | M |
| FR-4.15 | Permission changes should be auditable for compliance (roadmap — see [§8](#8-known-limitations--deferred-work)) | S |

See [RBAC.md](RBAC.md).

### FR-5: Folders

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-5.1 | The system shall organize documents in a hierarchical folder tree with unlimited nesting (self-referential `parent_id`) | M |
| FR-5.2 | Folder names shall be unique per `(org, parent)` and may not contain `.` (reserved for `dot_path`) | M |
| FR-5.3 | The system shall support moving/reparenting folders with **cycle prevention** and subtree `dot_path` rewrite (400 on a cycle) | M |
| FR-5.4 | The UI shall provide drag-and-drop folder moves and a virtualized folder tree | S |
| FR-5.5 | Deleting a folder shall be refused while it has child folders (409); documents in a deleted folder shall survive with `folder_id` set NULL | M |
| FR-5.6 | The UI shall provide a **Windows-Explorer-style resource browser** with Details/List/Small-icon/Large-icon views, sortable by Name/Type/Modified/Size (folders first), all virtualized | S |

### FR-6: Document Management

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-6.1 | The system shall allow users with contribute access to create documents by pasting text (rich text → Markdown) | M |
| FR-6.2 | The system shall allow **binary file upload** (`POST /api/documents/upload`, multipart) storing the original in S3-compatible object storage under `{org_id}/{document_key}/{filename}` before commit | M |
| FR-6.3 | Upload shall accept `.pdf, .png, .jpg, .jpeg, .tif, .tiff, .bmp, .gif, .webp, .txt, .md, .docx, .doc, .zip`, enforced by an extension allowlist and a `MAX_FILE_SIZE_MB` cap (bounded streaming read) | M |
| FR-6.4 | For image/PDF uploads the user shall choose a text-extraction method: **`ocr`** (Tesseract, free) or **`ai`** (OpenAI vision, paid) | M |
| FR-6.5 | `.zip` uploads shall be server-expanded into one document per member (≤200 members, total-uncompressed cap, zip-bomb guards) and report created/skipped in a batch response | S |
| FR-6.6 | The system shall track processing status through **PENDING → PROCESSING → SUCCESS / FAILED**, with structured `processing_details` on failure | M |
| FR-6.7 | The UI shall surface processing status via badges and poll (4–5 s) while any document is PENDING/PROCESSING | M |
| FR-6.8 | The system shall support metadata-only updates (title, folder, tags, permissions) that re-propagate derived tags/access-keys/title to existing vectors **without re-embedding** | M |
| FR-6.9 | The system shall support full content replacement (`PUT /api/documents/{id}/content`) that **re-chunks and re-embeds**, purging prior vectors first (re-ingest is not idempotent) | M |
| FR-6.10 | The system shall serve document content (`GET …/content`) typed as markdown/text/pdf/image/other, with presigned URLs for binary originals and an "Original" download | M |
| FR-6.11 | The system shall provide an in-explorer **Markdown editor** (CodeMirror split edit/preview) to create/edit `.md`/`.txt` documents, with a toolbar and contextual table editor | S |
| FR-6.12 | Deleting a document shall remove the Postgres row, best-effort delete the stored original, and best-effort remove its vectors/graph nodes | M |
| FR-6.13 | The system shall support flexible **tags** (org-scoped, unique per org), settable by any member | M |
| FR-6.14 | The system shall support org-defined **custom attributes** (freeform or picklist, optionally required) stored in the document `metadata` JSONB | S |
| FR-6.15 | Unfiled documents (`folder_id IS NULL`) shall be visible to org admins; the create modal shall offer a folder picker | M |

### FR-7: Document Processing Pipeline

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-7.1 | The system shall process documents **asynchronously** via Celery workers (Redis broker), decoupled from the request that created them | M |
| FR-7.2 | The worker shall extract text per file type: `.txt/.md` decoded directly; `.docx` via **mammoth**; `.doc` via **antiword** (`.extracted.txt` sidecar); PDFs/images via **Tesseract** or **OpenAI vision** per the chosen method | M |
| FR-7.3 | The system shall chunk text into ~**500-token** segments with **20-token** overlap, sentence-boundary aware (tokenizer `o200k_base`) | M |
| FR-7.4 | The system shall generate an **embedding** per chunk (`text-embedding-3-small`, 1536-dim) and store chunks in a per-tenant Qdrant collection with payload including text, summary, `chunk_order`, `document_key`, tags, `access_keys` | M |
| FR-7.5 | The system shall generate **hierarchical summaries** (per-chunk → recursively grouped → document-level); failures degrade to truncated/joined fallbacks | S |
| FR-7.6 | The system shall compute a document-level vector from the summary embedding, or the centroid of chunk embeddings as fallback | S |
| FR-7.7 | When knowledge-graph extraction is enabled, the system shall extract reified `(subject, predicate, object)` claims per chunk (parallel) and batch-insert them into Neo4j | S |
| FR-7.8 | Knowledge-graph extraction shall be toggleable per **org** (`Org.use_knowledge_graph`) and overridable per **document** (`Document.use_knowledge_graph`) | S |
| FR-7.9 | For AI OCR the worker shall resolve the per-org OpenAI key via an internal API endpoint, falling back to the central key; the per-org key shall **never** ride the Celery broker | M |
| FR-7.10 | The worker shall report terminal status back via the internal router; a callback failure shall not fail the ingest, but a missing internal key shall be logged loudly | M |
| FR-7.11 | The worker shall retry Brain POSTs only on **5xx / 429 / network** errors (4xx → FAILED); PDF/image pages capped at `MAX_OCR_PAGES` (default 100) | M |
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
| FR-9.1 | The system shall store extracted entities as Neo4j nodes labelled per tenant (`:Entity:Tenant_<id>`) and relationships as typed edges carrying `tags`, `access_keys`, `document_key` | S |
| FR-9.2 | During hybrid RAG the system shall retrieve up to 10 relationship facts relevant to the query and include them in the LLM context as an explicit "Knowledge Graph Relationships" block | S |
| FR-9.3 | Graph retrieval shall respect RBAC (access-key intersection; empty access = public) and folder/tag scope | M |
| FR-9.4 | Graph facts shall be removed when their source document or tenant is deleted | M |

> **Note:** current graph retrieval is a keyword/substring match over the tenant's
> facts, not multi-hop Cypher traversal — see [§8](#8-known-limitations--deferred-work).
> Design of the reified-claim fact store: [KNOWLEDGE_ENGINE.md](KNOWLEDGE_ENGINE.md).

### FR-10: Conversational AI / RAG

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-10.1 | The system shall answer natural-language questions from retrieved context using an LLM (`gpt-5-mini`, `temperature=0.3`, `max_tokens≈1000`) | M |
| FR-10.2 | The system shall run **hybrid retrieval**: vector chunk search (default top-5) plus optional knowledge-graph facts, deduped to unique source documents | M |
| FR-10.3 | The LLM shall answer **only** from provided context, with inline bracketed `[n]` citations and no outside knowledge | M |
| FR-10.4 | The system shall **stream** answers over Server-Sent Events with event types `sources`, `graph`, `delta`, `done`, `error` | M |
| FR-10.5 | The UI shall stream over `fetch` (to carry `Authorization`/`X-Org-ID`), render tokens incrementally, and turn `[n]` markers into per-passage citation links | M |
| FR-10.6 | The UI shall abort the stream (cancelling downstream LLM cost) on new-chat, delete, unmount, or a new send | M |
| FR-10.7 | The system shall persist chat sessions per user (`chat_data` JSONB; history clamped to ~10 turns when building the prompt), owner-scoped and soft-deletable | M |
| FR-10.8 | The user shall be able to **scope** a query to selected folders and/or tags ("All documents" when none); scope shall reset on org change | S |
| FR-10.9 | Retrieval shall always be entitlement-filtered by the user's access masks in addition to any explicit scope | M |

### FR-11: Document Reader & Help

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-11.1 | The system shall provide a full-screen reader with side-by-side (summary ↔ full text, **scroll-synced**) and embedded (inline per-chunk summary) modes | S |
| FR-11.2 | The reader shall lazily load chunks a page at a time (page size 50) to scale to book-length documents | S |
| FR-11.3 | The system shall render a navigable **summary tree** and an "indexed chunks" view showing how retrieval sees the document | S |
| FR-11.4 | The UI shall provide a **context-sensitive help panel** docked as a right rail on desktop (≥1024 px) and an overlay drawer on mobile, resolving help topics by route | C |
| FR-11.5 | All rendered untrusted content (chunk text, Markdown) shall be sanitized (DOMPurify) before display | M |

### FR-12: Custom Entities & Records

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-12.1 | Org admins shall define **custom entities** (name, fields, relationships) via `POST /api/entity-definitions`; the API runs physical Postgres DDL to create a `ce_<slug>` table matching the schema | M |
| FR-12.2 | Entity/field/relationship metadata shall be catalogued in `entity_definitions`, `entity_fields`, `entity_relationships` | M |
| FR-12.3 | Slug and identifier inputs shall be validated against SQL injection and Postgres reserved words before any DDL/DML (`services/identifiers.py`, `services/schema_manager.py`) | M |
| FR-12.4 | The system shall provide record CRUD (`GET/POST /api/entities/{slug}/records`, get/patch/delete by id) with **keyset (cursor) pagination** (opaque token over `(created_at, id)`; no OFFSET) and text search (`?q=`) | M |
| FR-12.5 | Record lists shall accept repeatable `filter=<field>:<op>[:<value>]` params (`eq/ne/gt/gte/lt/lte/in/contains/isnull`) and `order_by`/`order_dir`; keyset pagination shall work under any sort (composite cursor) | M |
| FR-12.6 | Filterable scalar fields shall be index-backed via per-field `(org_id, col DESC, id DESC)` btrees (migration 025), created `CONCURRENTLY` | S |
| FR-12.7 | Record access shall be scoped by org via RLS + explicit `org_id`; write authorization shall honour the entity/field access policies of [FR-4.12](#fr-4-access-control-rbac--entityfield-policies) | M |
| FR-12.8 | Relationship fields shall support 1:1 and 1:M links to other entities, resolvable in forms/views and reports | M |

### FR-13: Forms, Views & Dashboards

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-13.1 | Forms and views shall share one schema — a recursive **v2 element tree** (`{version:2, elements:[]}`) — rendered by one component (`FormRenderer`) across public-token, authenticated-fill, and builder-preview surfaces | M |
| FR-13.2 | The tree shall support element types: `field`, `label`, `calculated`, `button`, `section` (1:1), `table` (1:M grid, cross-entity editable columns), `block` (repeatable group), `record_list`, `input`, `live_value`, `report`, `form_ref`, and layout containers (`tab_group`/`panel`/`accordion`/`columns`) | M |
| FR-13.3 | Field control and validation shall derive from the entity field's own `field_type` — never author-chosen; the tree only tunes presentation and binding | M |
| FR-13.4 | `calculated` elements shall evaluate a **sandboxed** JsonLogic expression (whitelisted ops, no `eval`/attribute access); persisted calc values shall be **server-recomputed** (client-sent values ignored) | M |
| FR-13.5 | The system shall support public, unauthenticated **token links** (`GET/POST /api/public/forms/{token}`) that resolve the org from the token on a privileged session, then RLS-scope to it; links are single-use with optional recipient email + expiry (SHA-256-hashed token) | M |
| FR-13.6 | The system shall support authenticated internal fill (`GET /api/forms/{id}/render?record_id=` + `POST /api/forms/{id}/submit`, member-gated) and org-admin form/view CRUD (`/api/forms/*`, `/api/views/*`) | M |
| FR-13.7 | **Views** shall reuse the same tree as standalone screens/dashboards; standalone (no-entity) views allow only unbound/layout elements plus `record_list`/`report`/`live_value` | M |
| FR-13.8 | `record_list` shall render a read-only, optionally-polling "status board" of an entity's records (fields, sort, limit, `poll_ms`) with optional per-row link and per-row workflow button; polling pauses on hidden tabs and backs off on error | S |
| FR-13.9 | The `report` element shall embed a saved report on any view and render its chart/KPI/table | S |
| FR-13.10 | An org shall be able to designate a **home view** (migration 036) as its default landing screen | C |

See [FORMS_AND_VIEWS.md](FORMS_AND_VIEWS.md).

### FR-14: Workflow Automation

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-14.1 | The system shall provide a durable **workflow engine** with a **BPMN 2.0.2 token engine** and a retained legacy walker, selected per version (dual-engine cutover; existing published versions run unchanged) | M |
| FR-14.2 | A workflow version shall store a graph `{schema_version, nodes, edges}` validated structurally (Pydantic) and semantically (reachability, gateway arity, boundary attachment, loop-progress); the frontend mirrors the same rules | M |
| FR-14.3 | Published versions shall be **immutable** (DB trigger); authoring is draft-save then publish | M |
| FR-14.4 | The system shall support triggers: **`on_record_change`** (create/update/delete), **`on_form_submission`**, **scheduled** (timers), and **inbound webhook** | M |
| FR-14.5 | The system shall support actions including `update_record_field`, `create_record`, `get_record`/`update_record`, `send_email` (HTML template, recipient validated), `send_webhook`/HTTP request (allowlisted hosts, SSRF-guarded), `send_form`, plus AI actions (`knowledge_search`, `summarize`, `llm_respond`, `llm_grade`) | M |
| FR-14.6 | Record changes shall be written to `workflow_outbox` in the same transaction (at-least-once); a Celery-beat sweep shall dispatch pending events, claiming with `FOR UPDATE SKIP LOCKED` (exactly-once per event) under a per-event RLS role downgrade | M |
| FR-14.7 | The system shall support **`run_inline_on_change`** — firing an entity-change workflow synchronously in the mutating request (bounded time budget, deduped against the later sweep) — settable via `PATCH /workflows` (migrations 024/027) | S |
| FR-14.8 | The system shall support **manual runs** (`POST /api/workflows/{id}/run`) of the published version against declared inputs, gated by per-workflow `run_permission` (org_admin default; widenable to any_member or roles/groups); side-effecting actions shall be rejected on free-form (non-record) data, and record ownership validated | M |
| FR-14.9 | `workflow_runs` and `workflow_run_steps` (and `workflow_run_tokens`, migration 018) shall be RANGE-partitioned by `created_at`, with idempotent partition pre-creation | S |
| FR-14.10 | The system shall provide a **React-Flow designer**, an SSE live-run overlay, and run/step monitoring (`GET /api/workflows/{id}/runs`, `GET /workflows/runs/{run_id}/steps`) | S |
| FR-14.11 | The workflow engine shall run as a **privileged writer**, so it may write `workflow_only` entities and read `server_only` fields on behalf of the flow | M |

See [WORKFLOW_ENGINE.md](WORKFLOW_ENGINE.md).

### FR-15: Reporting & Aggregation

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-15.1 | The system shall run **GROUP BY / metric** queries over a custom entity (`POST /api/entities/{slug}/aggregate`): group by fields/relationships/base columns with optional date bucketing (`hour/day/week/month/quarter/year`); metrics `count/count_distinct/sum/avg/min/max`; `filters`, `having`, `order_by`, `limit` | M |
| FR-15.2 | Every group/metric field shall be whitelisted to a physical column and every op/bucket drawn from a closed set, so no user string reaches SQL as an identifier; the query runs under the tenant's RLS session | M |
| FR-15.3 | The system shall support **saved reports** (`reports` table, migration 026; `GET/POST/PATCH/DELETE /api/reports`, `POST /api/reports/{id}/run`, `POST /api/reports/run` ad-hoc) coupling an aggregate query with a visualization spec (`bar/stacked/line/area/pie/donut/scatter/table/metric`), validated at save; admin-gated writes, member-gated run | M |
| FR-15.4 | Reports shall be embeddable on dashboards via the `report` view element and shall travel (id-remapped) in the org import/export bundle | S |

### FR-16: AI Agent Organization & Governance

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-16.1 | The system shall support a per-tenant **agent organization**: a tree of agents (`supervisor_id`) with a governance **kind** (`coordinator`/`advisory`/`operator`, migration 030) that hard-caps usable tool categories | M |
| FR-16.2 | Every requested tool call shall be gated by an **authority engine** with precedence `deny > ask > allow > (default deny)`: kind-gate, availability (read always; write/execute require grants), per-agent approval-required, and a **high-touch autonomy overlay** (`orgs.agent_autonomy`, migration 033) that forces `ASK` on any side-effecting tool while internal record/document writes run free | M |
| FR-16.3 | All tool calls in a turn shall be authority-gated **before any executes**, so a run that parks on `ASK` has taken no partial side effects | M |
| FR-16.4 | The system shall provide two run paths sharing one loop: an **interactive console** (SSE, in-process, auto-approves live) and a **worker executor** (beat-driven, parks on `ASK` as an `AgentApproval` until a human resolves it) | M |
| FR-16.5 | Side-effecting actions shall funnel to a single **human approval inbox** (`/api/approvals`, approve/deny) with notifications | M |
| FR-16.6 | The system shall support **multi-provider** models via LiteLLM (Anthropic/OpenAI/Google), per-role **cost tiering**, per-org encrypted provider credentials (`POST /api/agents/providers/credentials`, migration 029), Anthropic **prompt caching**, and read-only research/batch tools (`web_research` via Gemini grounding, `batch_generate`) | S |
| FR-16.7 | The system shall provide **work orders** (`work_orders` router) that members file and that optionally kick off the assigned supervisor agent as a queued run | S |
| FR-16.8 | The system shall provide an **agent scheduler** (cron `agent_schedules` swept via internal endpoint + beat) and an idempotent **autonomous-company provisioner** (`scripts/provision_company.py`) that stands up a full traditional org of agents under high-touch governance | S |
| FR-16.9 | An opt-in `run_claude_code` tool (off by default, `CLAUDE_CLI_TOOL_ENABLED`) shall offload dev/ops work to a local Claude Code CLI under strict guardrails (allow-listed working dir, read-only default tools, kill-on-timeout) | C |

See [AGENT_ORG.md](AGENT_ORG.md).

### FR-17: In-App Authoring Assistant

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-17.1 | The system shall provide an org-admin-gated, in-process tool-calling assistant (`POST /api/agent/chat/stream`, SSE) that manages authoring artifacts (entities, forms, views, workflows) through the same validation the UI uses | M |
| FR-17.2 | The assistant shall use progressive-disclosure tooling (e.g. `describe_form_elements`, `describe_workflow_actions`, dry-run validation with located errors) so config edits are validated before commit | S |
| FR-17.3 | The assistant shall decrypt the org's stored provider key per request, falling back to the central key; tool commits shall be atomic and run on short-lived sessions (no pool saturation) | M |

### FR-18: Enterprise API & API Keys

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-18.1 | Org admins shall create/list/revoke **org API keys** (`GET/POST/DELETE /api/api-keys`, `GET /api/api-keys/scopes`), storing only the SHA-256 **hash** of a `km2_…` key (plaintext shown once, never logged or returned) with a scope set and optional expiry (migration 028, `FORCE ROW LEVEL SECURITY`) | M |
| FR-18.2 | The system shall expose a stable, versioned **`GET/POST /api/v1/**`** surface reusing the same services as the first-party UI: entities (read), records (CRUD + aggregate with inline-workflow dispatch), reports, workflows, search + RAG chat, knowledge base, and config receive | M |
| FR-18.3 | Access shall be gated by **scopes**: `entities:read`, `records:read`, `records:write`, `reports:read`, `reports:run`, `workflows:read`, `workflows:run`, `search:read`, `knowledge:read`, plus `config:read`/`config:write` (write excluded from `*`/`domain:*` wildcards); org service keys act with org-wide data visibility | M |
| FR-18.4 | The `/api/v1` surface shall enforce **rate limiting**: per-key Redis fixed-window quota (`API_KEY_RATE_LIMIT_PER_MINUTE`, default 600) with `X-RateLimit-*`/`Retry-After` headers, plus a coarse pre-auth per-IP guard (`API_IP_RATE_LIMIT_PER_MINUTE`, default 1200); both fail open on a Redis outage | M |
| FR-18.5 | The system shall publish Swagger UI at `/api/v1/docs` (+ `/api/v1/openapi.json`) covering only the public surface, gated by `API_DOCS_ENABLED`; the internal `/docs` stays debug-only | S |

### FR-19: Integrations — MCP, Webhooks & Connections

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-19.1 | The system shall accept **inbound webhooks** that start a workflow run, authenticated by an opaque URL token plus optional HMAC `X-KM2-Signature` (migration 022); inbound runs execute inline | S |
| FR-19.2 | The system shall support **outbound connections** — saved connector credentials (`bearer`/`api_key`/`basic`) that workflows/forms call third-party HTTP APIs (and robots) with, host-allowlisted and SSRF-guarded | S |
| FR-19.3 | The system shall let agents connect to **external MCP servers** via an OAuth 2.1 (PKCE) "Connect" flow (org- or user-scoped, tokens Fernet-encrypted; migration 032), pre-authorizable by `mcp__<server>__*` grants | S |
| FR-19.4 | The system shall provide the `km2-mcp` **developer tool** (`tools/km2-mcp`) that drives the REST API over a live Clerk browser session — a dev/automation tool, not part of the deployed product, with no privilege elevation | C |

See [MCP_AND_INTEGRATIONS.md](MCP_AND_INTEGRATIONS.md).

### FR-20: Import/Export & Change Management

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-20.1 | The system shall export/import an org's configuration as a JSON **bundle** (entities, forms, views, workflows, reports, records, documents) with deep id-remapping via `GET /api/migration/manifest`, `POST /api/migration/export`, `POST /api/migration/import` — secrets are **never** exported | M |
| FR-20.2 | Import shall support skip/overwrite/rename strategies and a **diff** preview (`POST /api/migration/diff`) | S |
| FR-20.3 | Every configurable object shall carry a durable cross-environment `lineage_id` (migration 037) so a promoted copy stays linked to its source across renames/re-imports | M |
| FR-20.4 | The system shall support **release promotion** (migration 038): cut a frozen `release`, move it through review/approval, **promote** it to another environment (another org in this DB, or a remote KM2 instance), and **roll back** — via the `PromotionService`, `/api/promotions`, and `/api/migration` | S |
| FR-20.5 | Cross-org (`local_org`) promotion shall additionally require the caller to administer the target org; remote promotion shall push over a **config transport** authenticated by a remote org API key (`Bearer`), SSRF-guarded, HTTPS-only, received at `/api/v1/config/*` | M |
| FR-20.6 | The UI shall provide a change-management console (`/change-management`), release detail/diff preview, and a Site Admin → Deployments tab | S |

See [CHANGE_MANAGEMENT.md](CHANGE_MANAGEMENT.md).

### FR-21: Theming & UX

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-21.1 | The UI shall support **Light / Dark / Red Arch** themes, persisted in `localStorage` and applied pre-paint (no flash); first visit follows the OS preference | C |
| FR-21.2 | The UI shall surface success/error feedback via toasts and shall not 500 an already-committed document when the broker is unavailable at enqueue time | M |

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
| NFR-1.6 | Blocking clients (Qdrant/Neo4j/OpenAI) in Brain API run off the event loop (`asyncio.to_thread`) | — |
| NFR-1.7 | Record lists use keyset (cursor) pagination — no OFFSET — with per-field btree indexes for filterable scalars | — |

### NFR-2: Scalability

| ID | Requirement | Target |
|----|-------------|--------|
| NFR-2.1 | Concurrent users per org | 100+ |
| NFR-2.2 | Documents per org | 100,000+ |
| NFR-2.3 | Total chunks across tenants | 10M+ |
| NFR-2.4 | API services shall be stateless and horizontally scalable | Stateless design |
| NFR-2.5 | Workers shall scale independently (Redis broker, `acks_late`, prefetch=1) | Independent pool |
| NFR-2.6 | The UI file explorer/tree shall virtualize large lists (`react-window`) | — |
| NFR-2.7 | High-volume tables (`workflow_runs`/`_steps`/`_tokens`) are RANGE-partitioned by month with idempotent pre-creation | — |

### NFR-3: Security

| ID | Requirement | Implementation |
|----|-------------|----------------|
| NFR-3.1 | Endpoints authenticated (except health, setup status, public form tokens) | Clerk JWT / `km2_…` API key / E2E header |
| NFR-3.2 | Tenant data isolation | PostgreSQL RLS (FORCE) + explicit `org_id` filtering |
| NFR-3.3 | No cross-tenant leakage in vector/graph search | Per-tenant collections + `access_keys` filtering + tenant labels |
| NFR-3.4 | Distinct, non-shared service secrets | Clerk JWT (user), `BRAIN_API_KEY` (API→Brain), `INTERNAL_API_KEY` (worker→API) |
| NFR-3.5 | Internal-key comparison constant-time | `hmac.compare_digest`; empty key → 503 (disabled, not open) |
| NFR-3.6 | API keys stored hashed, plaintext shown once | SHA-256 hash (migration 028); never logged/returned on read |
| NFR-3.7 | Provider keys + OAuth tokens encrypted at rest | Fernet symmetric (`ORG_ENCRYPTION_KEY`); migrations 016/029/032 |
| NFR-3.8 | Outbound HTTP (webhooks/connections/config-push) SSRF-guarded | Host allowlist + private-range guard; HTTPS on remote transport |
| NFR-3.9 | Agent side effects require human approval | Authority engine `deny>ask>allow`, high-touch default (migration 033) |
| NFR-3.10 | Tamper-proof record surfaces | Per-entity `write_access` + per-field `read_access` (migration 039); only privileged sessions bypass |
| NFR-3.11 | No untrusted string reaches SQL as an identifier | Slug/reserved-word validation; whitelisted aggregate columns; closed op/bucket sets |
| NFR-3.12 | Rate limiting on the public API | Per-key + per-IP Redis fixed-window (fail-open on Redis outage) |
| NFR-3.13 | Secrets via env / secret manager, never hardcoded | 12-factor; required secrets enforced (`${VAR:?}`) in prod compose |
| NFR-3.14 | Input validation at boundaries; output sanitization in UI | Pydantic schemas; extension allowlist; zip-bomb guards; DOMPurify |
| NFR-3.15 | Security headers on UI responses | `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy` |
| NFR-3.16 | Secret scanning in CI/pre-commit | gitleaks; `.env*` gitignored |

### NFR-4: Reliability

| ID | Requirement | Target |
|----|-------------|--------|
| NFR-4.1 | System availability | 99.5% uptime |
| NFR-4.2 | Data durability | PostgreSQL with backups |
| NFR-4.3 | Graceful degradation on Brain API failure | Best-effort cascades log and continue; Postgres delete not blocked |
| NFR-4.4 | Worker task retry with bounded backoff | 5xx/429/network only; max 3 retries; soft/hard limits 1740/1800 s |
| NFR-4.5 | Enqueue failure shall not lose a committed document | Doc left PENDING for reconciliation; request still 201 |
| NFR-4.6 | Pooled-connection safety of RLS | Transaction-local `SET LOCAL ROLE` + `set_config`, auto-reset |
| NFR-4.7 | Workflow dispatch exactly-once per event | `FOR UPDATE SKIP LOCKED` claim; outbox at-least-once + idempotent `ON CONFLICT`; pg_advisory_lock on scheduled runs |
| NFR-4.8 | Agent runs take no partial side effects on park | All tool calls gated before any executes; parked `ASK` runs resume on approval |

### NFR-5: Maintainability

| ID | Requirement | Implementation |
|----|-------------|----------------|
| NFR-5.1 | Test coverage (Python) | 80% minimum, CI-gated (`--cov-fail-under=80`) |
| NFR-5.2 | Type safety | mypy `strict` (Python); statically typed Go (migration target) |
| NFR-5.3 | Lint/format | ruff (line length 120) + ruff-format; ESLint 9 (UI) |
| NFR-5.4 | Structured logging | Single-line JSON with OTel `trace_id`/`span_id` correlation |
| NFR-5.5 | Health endpoints | `/healthz` on all services (`/readyz` partial — see §8) |
| NFR-5.6 | Test pyramid | pytest unit/integration; Vitest component; Playwright E2E |
| NFR-5.7 | Shared implementation across surfaces | `/api/v1` and internal routers reuse one set of services (records/search/workflow helpers) |

### NFR-6: Deployment & Observability

| ID | Requirement | Implementation |
|----|-------------|----------------|
| NFR-6.1 | Container-based deployment | Docker images per service |
| NFR-6.2 | Local development | Docker Compose + hybrid `run-stack.sh` (host uvicorn/Next + dockerized infra) |
| NFR-6.3 | CI/CD | GitHub Actions (lint, type-check, python/go/ui tests, E2E, security) |
| NFR-6.4 | Database migrations | **Alembic** (Python, authoritative, through 039); golang-migrate (Go port) |
| NFR-6.5 | Configuration | Environment variables (12-factor) |
| NFR-6.6 | Tracing/metrics | OpenTelemetry (OTLP) + Prometheus instrumentator; Flower for Celery |

---

## 5. External Dependencies

| Dependency | Version | Purpose |
|------------|---------|---------|
| PostgreSQL | 18 (host port 5433) | Primary data store with RLS |
| Redis | 7.4 | Celery broker/result backend, setup-token store, rate limits |
| Qdrant | 1.12.4 | Vector similarity search |
| Neo4j | 5.25.1 (+ APOC) | Knowledge graph |
| MinIO / S3 | — | Object storage for original uploaded files |
| Clerk | SaaS | Identity provider (OIDC) for end users |
| OpenAI API | — | Embeddings, chat completions, summaries, claim extraction, AI OCR, in-app assistant |
| Anthropic API | — | Agent-org models (via LiteLLM), prompt caching, batch generation |
| Google Gemini API | — | Agent `web_research` (Google Search grounding, free tier) |
| LiteLLM | — | Multi-provider model routing for the agent org |
| Tesseract / poppler / antiword | — | Free OCR and legacy document text extraction (worker image) |

**Models:** chat/summary/claims `gpt-5-mini`; embeddings `text-embedding-3-small`
(1536-dim); AI OCR `gpt-4.1-mini` (vision); agent org tiered
Opus/Sonnet/Haiku (Anthropic), `gpt-5*` (OpenAI), `gemini-2.5-*` (Google).

---

## 6. Configuration Requirements

Required secrets (no safe default): `POSTGRES_PASSWORD`, `NEO4J_PASSWORD`, `OPENAI_API_KEY`,
`STORAGE_SECRET_KEY`, `API_SECRET_KEY`, `BRAIN_API_KEY`, `INTERNAL_API_KEY`,
`ORG_ENCRYPTION_KEY`, `CLERK_JWT_ISSUER`, `CLERK_ALLOWED_AZP` (when Clerk enabled),
`CLERK_SECRET_KEY`, `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`.

Provider keys for the agent org (per-org override encrypted at rest, else central):
`ANTHROPIC_API_KEY`, `GEMINI_API_KEY`.

Key tunables: `MAX_FILE_SIZE_MB` (50), `MAX_OCR_PAGES` (100), `OPENAI_CHAT_MODEL`,
`OPENAI_EMBEDDING_MODEL`, `OPENAI_OCR_MODEL`, `BRAIN_MAX_TOKENS` (16000),
`API_KEY_RATE_LIMIT_PER_MINUTE` (600), `API_IP_RATE_LIMIT_PER_MINUTE` (1200),
`API_DOCS_ENABLED`, `WORKFLOW_TOKEN_ENGINE_ENABLED` (on), `CLAUDE_CLI_TOOL_ENABLED` (off),
`AGENT_WEB_RESEARCH_MODEL`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `LOG_LEVEL`. See
[`.env.example`](../.env.example) for the full, grouped list.

> Note: `API_RATE_LIMIT_PER_MINUTE` (main-API) is configured but no limiter is
> currently mounted on the first-party API — see [§8](#8-known-limitations--deferred-work).
> Rate limiting **is** live on `/api/v1` (FR-18.4).

---

## 7. Reference Applications

The **LMS, HRMS, and ticketing** capabilities are **reference applications assembled
from the generic platform primitives** (custom entities, forms/views, workflows,
entity access control, RAG, agent org) plus a small number of platform additions and
seed data — **not** bespoke modules. There is no `courses`, `enrollments`, or
`work_orders`-domain table beyond the generic dynamic-entity storage (ticketing/work
orders being the one exception, backed by the agent-org `work_orders` router).

| App | Live example org | Built from | Reference |
|-----|------------------|-----------|-----------|
| **LMS** (courses, quizzes, LLM-graded scenarios, certificates) | "Corporate Training" | Custom entities + learner-bound views (`@me` filter) + server-graded quiz + LLM-graded scenario workflows + tamper-proof answer keys + admin course generator | [LMS.md](LMS.md) |
| **HRMS** (pre-hire/onboarding/offboarding, reviews) | "Human Resource Management" | 13 custom entities + 7 workflows + forms/views + reports across dashboards | — |
| **Ticketing / work orders** | Agent-org demos | `work_orders` router — members file, supervisor agent optionally auto-runs | [AGENT_ORG.md](AGENT_ORG.md) |

These illustrate that domain applications are configured, not coded; nothing in the
schema is LMS/HRMS-specific.

---

## 8. Known Limitations & Deferred Work

Truthfully-documented gaps between the aspirational feature set and the current code:

| Area | Limitation |
|------|------------|
| **Knowledge graph** | Retrieval is a case-insensitive **substring/keyword match** over all of a tenant's triples (then top-10), **not** multi-hop Cypher traversal. Fetch-all-then-filter is also a scale concern (cf. NFR-2.3) |
| **`/readyz`** | Returns a static OK on the Python API — real dependency probes are deferred (REDARCH-12) |
| **Main-API rate limiting** | `API_RATE_LIMIT_PER_MINUTE` is configured but no limiter middleware is mounted on the first-party API; `/api/v1` **is** rate-limited (FR-18.4) |
| **Chat stream timeout** | The API→Brain streaming proxy has no request timeout (REDARCH-14) |
| **Re-ingest idempotency** | Brain ingest is not inherently idempotent (fresh UUIDs per run); the API compensates by purging vectors before re-ingest. Worker `acks_late` means a crash can re-run extraction (not exactly-once) |
| **Per-org provider key** | Worker AI OCR and the agent org resolve the per-org key; the Brain API's embeddings/summaries/claims/RAG chat still use the central OpenAI key |
| **Audit logging** | Permission-change audit (FR-4.15) is a Should-Have, not yet a durable trail |
| **Go stack** | The Go rewrite lacks chat/RAG, search proxy, setup, site-admin, entities/forms/views/workflows/reports/agents/change-management surfaces, uses **asynq** (not interoperable with Celery), and is absent from prod/`run-stack.sh` |
| **`auth_subject` rename** | `user_profiles.keycloak_sub` still holds the Clerk subject; the column rename is a deferred migration |

---

## 9. Constraints

| ID | Constraint |
|----|------------|
| C-1 | The shipping backend is **Python** (FastAPI, async SQLAlchemy, Celery). A Go port is a directional goal but **not** yet authoritative |
| C-2 | Frontend is **Next.js 15** (App Router) with **React 18**, TypeScript, Tailwind v4; state via React Context + axios (no React Query) |
| C-3 | Must use the existing PostgreSQL schema with RLS and the `app_user`/`app_admin` role model |
| C-4 | Must authenticate end users exclusively via **Clerk** (Keycloak fully removed as of Slice 6); the `/api/v1` surface uses org API keys |
| C-5 | Must preserve the REST API contract the UI depends on |
| C-6 | The Python and Go task pipelines are **not** interoperable (Celery vs asynq) — run one full stack or the other, never mixed |
| C-7 | Object-storage endpoints must not contain underscores (botocore rejects them) |
| C-8 | Published workflow versions and released config are **immutable** (DB trigger / frozen bundle); changes are new versions/releases |
| C-9 | Custom-entity DDL runs against the live schema; entity/field/slug identifiers are validated and cannot use reserved words |
| C-10 | The agent fleet runs on provider **API keys** (Anthropic/OpenAI/Gemini via LiteLLM); high-touch autonomy is the default posture |

---

## 10. Out of Scope

The following are explicitly **not** current requirements:

- **Turnkey vertical modules** — LMS/HRMS/ticketing ship as reference configurations
  and seed data (§7), not as installable, self-contained product modules.
- **Real-time collaborative editing** of documents/forms/views (single-writer today).
- **Self-hosted identity** — Keycloak and other IdPs were removed; Clerk is the sole
  end-user IdP (C-4).
- **Cross-stack mixing** — running Python and Go pipelines against one database (C-6).
- **Fine-grained per-user API keys** — API keys are org service keys with org-wide
  data visibility, gated only by scope (FR-18.3).
- **Exactly-once document ingestion** — the pipeline is at-least-once with
  purge-before-reingest compensation (§8).

---

## 11. Related Documentation

| Document | Covers |
|----------|--------|
| [FEATURES.md](FEATURES.md) | Capability overview (narrative) |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Service boundaries, data flow, Go migration status |
| [RBAC.md](RBAC.md) | 32-bit access-mask model and permission calculation |
| [DATABASE.md](DATABASE.md) | Schema, RLS policies, relationships |
| [API.md](API.md) | REST endpoints with request/response examples |
| [WORKFLOW_ENGINE.md](WORKFLOW_ENGINE.md) | BPMN token engine, designer, dispatch |
| [FORMS_AND_VIEWS.md](FORMS_AND_VIEWS.md) | v2 element tree, renderer, surfaces |
| [AGENT_ORG.md](AGENT_ORG.md) | Agent governance, runtime, model tiers, autonomous company |
| [CHANGE_MANAGEMENT.md](CHANGE_MANAGEMENT.md) | Release promotion, lineage, targets |
| [MCP_AND_INTEGRATIONS.md](MCP_AND_INTEGRATIONS.md) | MCP, inbound/outbound webhooks, config transport |
| [KNOWLEDGE_ENGINE.md](KNOWLEDGE_ENGINE.md) | Reified-claim fact store design |
| [SITE_ADMIN.md](SITE_ADMIN.md) | Cross-org operator console |
| [LMS.md](LMS.md) | LMS reference application |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Docker Compose, secrets, scaling, backup/restore |
| [DEVELOPMENT.md](DEVELOPMENT.md) | Local setup, make commands, debugging |
| [README](../README.md) | Project overview and quick start |

---

> Reviewed 2026-07-16 against Alembic migration 039. Source of truth is the code; if this doc disagrees with the code, the code wins.
