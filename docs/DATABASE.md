# Database

The Red Arch Knowledge Management Platform v2 (KM2) stores all relational state
in **PostgreSQL 18** with **Row-Level Security (RLS)** for multi-tenant isolation.
This doc is the schema reference for engineers and technical evaluators: the
tables by domain, the migration that introduced each, its RLS posture, indexes,
cascade behavior, and how migrations and backups are run. Vectors, the knowledge
graph, and object storage live outside Postgres (Qdrant, Neo4j, MinIO) and are
covered in `[ARCHITECTURE.md](ARCHITECTURE.md)` and
`[KNOWLEDGE_ENGINE.md](KNOWLEDGE_ENGINE.md)`.

The schema is defined entirely by Alembic migrations in
`services/api/alembic/versions/`, currently through **039_entity_access_control**.
The migrations are the source of truth; this doc summarizes them.

## Table of Contents

- [Connection](#connection)
- [Schema Overview](#schema-overview)
  - [Identity, Orgs & Memberships](#identity-orgs--memberships)
  - [Permission Dimensions (RBAC)](#permission-dimensions-rbac)
  - [Documents & Knowledge Base](#documents--knowledge-base)
  - [Custom Entities & Records](#custom-entities--records)
  - [Forms & Views](#forms--views)
  - [Workflows, Connections, Inbound & Outbox](#workflows-connections-inbound--outbox)
  - [Reports](#reports)
  - [API Keys](#api-keys)
  - [Agents Domain](#agents-domain)
  - [MCP Servers & OAuth](#mcp-servers--oauth)
  - [LLM Provider Credentials](#llm-provider-credentials)
  - [Config Lineage & Release Management](#config-lineage--release-management)
  - [Entity Access Control](#entity-access-control)
- [Row-Level Security](#row-level-security)
- [Database Roles & Sessions](#database-roles--sessions)
- [Migrations](#migrations)
- [Indexes](#indexes)
- [Cascade Behavior](#cascade-behavior)
- [Backup & Recovery](#backup--recovery)
- [Known gaps / TODO](#known-gaps--todo)

## Connection

The application connects as the non-superuser role `km_app` so RLS is actually
enforced (see [Database Roles & Sessions](#database-roles--sessions)). Migrations
and manual `psql` run as the admin `POSTGRES_USER` (default `redarch`), which owns
the schema.

```bash
# Runtime app connection (from .env.example DATABASE_URL)
postgresql+asyncpg://km_app:${KM_APP_PASSWORD}@postgres:5432/redarch_km

# Migrations / admin (make migrate uses DATABASE_URL_LOCAL as POSTGRES_USER)
postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@localhost:5433/redarch_km

# Interactive psql — host port 5433 -> container 5432 in dev
psql -h localhost -p 5433 -U redarch -d redarch_km
```

Config is read from `DATABASE_URL` (`services/api/src/api/config.py`,
`Settings.database_url`). The dev roles/passwords are provisioned by
`docker/init-db.sql` on a fresh Postgres volume; production (Cloud SQL) provisions
`km_app` via Terraform and grants its memberships in a bootstrap step (see
migration 035).

## Schema Overview

Every tenant-scoped table carries an `org_id` FK to `orgs` (`ON DELETE CASCADE`)
and is protected by RLS keyed on `app.current_tenant_id`. `orgs` and
`user_profiles` are the cross-tenant roots and are **not** RLS-scoped. The table
below indexes each domain to the migration that introduced its core tables; the
per-domain subsections give column detail.

| Domain | Core tables | Introduced | RLS |
|---|---|---|---|
| Identity, orgs & memberships | `orgs`, `user_profiles`, `user_org_memberships` | 001 | memberships only |
| Permission dimensions (RBAC) | `regions`, `departments`, `roles`, `groups` + `membership_*` junctions | 001 | dimensions yes; junctions no |
| Documents & knowledge base | `folders`, `documents`, `tags`, `document_tags`, `document_access`, `document_attribute_definitions`, `chat_sessions` | 001 | yes (junctions no) |
| Custom entities & records | `entity_definitions`, `entity_fields`, `entity_relationships`, `ce_*` / `cej_*` | 008 (+ runtime DDL) | yes |
| Forms & views | `forms`, `form_links`, `views` | 011, 021 | yes |
| Workflows | `workflows`, `workflow_versions`, `workflow_outbox`, `workflow_runs`, `workflow_run_steps`, `workflow_run_tokens` | 009, 018 | yes |
| Connections & inbound | `workflow_connections`, `workflow_inbound_endpoints` | 019, 020 | yes |
| Reports | `reports` | 026 | yes |
| API keys | `api_keys` | 028 | yes |
| Provider credentials | `org_provider_credentials` | 029 | yes |
| Agents domain | `agents`, `mcp_servers`, `work_orders`, `work_order_tasks`, `work_order_artifacts`, `agent_runs`, `agent_run_steps`, `agent_approvals`, `agent_notifications`, `agent_schedules`, `work_order_entries` | 030 | yes |
| MCP OAuth | `mcp_server_user_tokens`, `mcp_oauth_flows` (+ `mcp_servers` cols) | 032 | yes |
| Release management | `promotion_targets`, `releases`, `release_items`, `release_approvals`, `release_promotions` | 038 | yes + admin bypass |

### Identity, Orgs & Memberships

#### orgs
Multi-tenant root. Not RLS-scoped — visible to all authenticated users (an org's
data is protected by RLS on the *tenant* tables, not on `orgs` itself).

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| name | VARCHAR(200) | Unique org name |
| description | TEXT | Optional |
| use_knowledge_graph | BOOLEAN | Enable Neo4j graph features (default true) |
| openai_api_key | VARCHAR(800) | Per-org OpenAI key, **Fernet-encrypted at rest** (migration 016; `ORG_ENCRYPTION_KEY`) |
| permission_number | SMALLINT | Org-level permission slot |
| agent_notify_workflow_id | UUID | Optional workflow fired on agent escalations (migration 031; no FK) |
| agent_autonomy | VARCHAR(16) | `high_touch` \| `balanced` \| `hands_off` (migration 033; default `high_touch`) |
| home_view_id | UUID | Optional landing view id (migration 036; no FK) |
| created_at / updated_at | TIMESTAMPTZ | |

`agent_notify_workflow_id` and `home_view_id` are plain UUIDs (no FK) deliberately
— a second FK path from `orgs` to `workflows`/`views` would make ORM joins
ambiguous; a stale id simply no-ops the feature.

#### user_profiles
User identities synced from Clerk. Not RLS-scoped (managed cross-org by site
admins). See `[AUTHENTICATION.md](AUTHENTICATION.md)`.

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| auth_subject | VARCHAR(255) | Clerk subject id (unique; renamed from `keycloak_sub` in migration 003) |
| username | VARCHAR(150) | Unique |
| email | VARCHAR(254) | Unique |
| description | TEXT | Optional |
| is_site_admin | BOOLEAN | Platform-wide admin flag |
| is_active | BOOLEAN | Default true; deactivated users are rejected at auth (403) even with a valid Clerk JWT (migration 004) |
| created_at / updated_at | TIMESTAMPTZ | |

#### user_org_memberships
Links users to orgs. **RLS-scoped** (has `org_id`). See `[RBAC.md](RBAC.md)` for
how memberships resolve to permissions.

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| profile_id | UUID | FK `user_profiles` (CASCADE) |
| org_id | UUID | FK `orgs` (CASCADE); RLS scope |
| is_org_admin | BOOLEAN | Org-level admin flag |
| created_at / updated_at | TIMESTAMPTZ | |

**Unique:** `(profile_id, org_id)`.

### Permission Dimensions (RBAC)

Four dimension tables scope permissions within an org (migration 001), each
RLS-scoped:

- `regions` — geographic regions
- `departments` — business departments
- `roles` — job roles
- `groups` — custom groups

Each has: `id` (PK), `name` VARCHAR(255), `description` TEXT, `permission_number`
SMALLINT (slot 0–31), `org_id` FK, timestamps. **Unique:** `(org_id, name)`.

**Membership junctions** (`membership_regions`, `membership_departments`,
`membership_roles`, `membership_groups`) link a membership to a dimension. Each has
a composite PK `(membership_id, {dim}_id)` and a secondary index on the dimension
id for reverse lookups. Junctions have **no `org_id`** and are **not** RLS-scoped —
isolation is inherited through the FK to `user_org_memberships` (itself RLS-scoped)
and enforced by application-level org filtering. See `[RBAC.md](RBAC.md)` for the
bitmask permission model.

### Documents & Knowledge Base

#### folders
Hierarchical document organization with permission masks (migration 001).

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| name | VARCHAR(255) | |
| description | TEXT | |
| order | INTEGER | Display order |
| dot_path | TEXT | Materialized path (e.g. `a.b.c`), indexed |
| view_permission_masks | BIGINT[] | Viewer access masks (default `{}`) |
| contributor_permission_masks | BIGINT[] | Contributor access masks (default `{}`) |
| viewer_permissions_config | JSONB | Human-readable viewer config |
| contributor_permissions_config | JSONB | Human-readable contributor config |
| org_id | UUID | FK `orgs` (CASCADE); RLS scope |
| parent_id | UUID | FK self (CASCADE), nullable |
| created_at / updated_at | TIMESTAMPTZ | |

**Unique:** `(org_id, name, parent_id)`.

#### documents
Knowledge base documents (migration 001; extended by 005, 015, 017).

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| title | VARCHAR(255) | |
| description | TEXT | |
| text | TEXT | Content |
| document_key | VARCHAR(255) | Unique per org (`uq_doc_key_per_org`); aligns with brain-api slugs |
| document_url | VARCHAR(2048) | External URL |
| processing_status | VARCHAR(20) | Default `PENDING` (PENDING/PROCESSING/COMPLETE/ERROR/…) |
| processing_details | JSONB | Processing metadata |
| metadata | JSONB | Custom attributes |
| use_knowledge_graph | BOOLEAN | Override org default (nullable) |
| size_bytes | BIGINT | Original size, nullable (migration 005) |
| celery_task_id | VARCHAR(64) | Ingest job id for cancel/log correlation (migration 017) |
| view_permission_masks | BIGINT[] | Per-doc viewer masks — empty = inherit folder (migration 015) |
| contributor_permission_masks | BIGINT[] | Per-doc contributor masks — empty = inherit folder (migration 015) |
| viewer_permissions_config | JSONB | Per-doc viewer config — NULL = inherit folder (migration 015) |
| contributor_permissions_config | JSONB | Per-doc contributor config — NULL = inherit folder (migration 015) |
| org_id | UUID | FK `orgs` (CASCADE); RLS scope |
| folder_id | UUID | FK `folders` (SET NULL) |
| uploaded_by_id | UUID | FK `user_profiles` (SET NULL) |
| created_at / updated_at | TIMESTAMPTZ | |

**Per-document permission precedence (migration 015):** if a per-document config is
non-NULL it overrides the folder's masks for that document; NULL falls back to the
folder. Mirrors the four permission columns on `folders`. See `[RBAC.md](RBAC.md)`.

#### tags / document_tags
`tags` (migration 001): `id`, `name` VARCHAR(100), `org_id`, timestamps; unique
`(org_id, name)`. `document_tags` is the M2M join (composite PK
`(document_id, tag_id)`, both FK CASCADE), with a secondary index on `tag_id`; it
has no `org_id` and is not RLS-scoped.

#### document_access / document_attribute_definitions
Both migration 001, RLS-scoped. `document_access` grants a user access to a folder
(`id`, `user_id` FK CASCADE, `folder_id` FK CASCADE, `org_id`, unique
`(user_id, folder_id)`). `document_attribute_definitions` defines per-org custom
document attributes (`name`, `slug`, `attribute_type` default `freeform`,
`picklist_options` JSONB, `required`, `order`, `org_id`; unique `(org_id, slug)`).

#### chat_sessions
Per-user chat history (migration 001; hardened by 006). RLS-scoped.

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| chat_data | JSONB | Message history |
| deleted | BOOLEAN | Soft-delete flag |
| org_id | UUID | FK `orgs` (CASCADE); RLS scope |
| user_id | UUID | FK `user_profiles`, **NOT NULL, ON DELETE CASCADE** (migration 006) |
| created_at / updated_at | TIMESTAMPTZ | |

Migration 006 switched `user_id` from `SET NULL` to `CASCADE` and made it NOT NULL
(a user-less session is unreachable by design), deleting orphaned rows first.

### Custom Entities & Records

The custom-entities catalog (migration 008) describes user-defined entity types.
The **physical per-entity tables are created at request time** by `SchemaManager`
(`services/api/src/api/services/schema_manager.py`), not by a migration.

#### entity_definitions
| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| name | VARCHAR(100) | Human-readable |
| slug | VARCHAR(63) | Unique per org (`uq_entity_def_slug_per_org`) |
| physical_table | VARCHAR(63) | Generated `ce_<hex32>` table name; globally unique |
| description | TEXT | |
| is_active | BOOLEAN | Soft-delete flag |
| write_access | VARCHAR(20) | `member` (default) or `workflow_only` (migration 039) |
| org_id | UUID | FK `orgs` (CASCADE); RLS scope |
| lineage_id | UUID | Cross-env promotion identity, nullable (migration 037) |
| created_at / updated_at | TIMESTAMPTZ | |

#### entity_fields
| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| name | VARCHAR(100) | |
| slug | VARCHAR(63) | Unique per definition (`uq_entity_field_slug_per_def`) |
| physical_column | VARCHAR(63) | Column name in the `ce_*` table |
| field_type | VARCHAR(30) | text, integer, bigint, decimal/numeric, boolean, date, datetime, single/multi_select, etc. |
| picklist_options | JSONB | Enum values for select types |
| is_required | BOOLEAN | NOT NULL constraint on the physical column |
| is_unique | BOOLEAN | Unique constraint on the physical column |
| default_value | JSONB | Optional default |
| order | INTEGER | Field order |
| read_access | VARCHAR(20) | `member` (default) or `server_only` (migration 039) |
| entity_definition_id | UUID | FK `entity_definitions` (CASCADE) |
| org_id | UUID | FK `orgs` (CASCADE); RLS scope |
| lineage_id | UUID | Migration 037 |
| created_at / updated_at | TIMESTAMPTZ | |

#### entity_relationships
`id`, `name`, `slug`, `cardinality`, `on_delete` (default `SET NULL`),
`physical_name` (`r_<hex32>` FK column or `cej_<hex32>` join table),
`is_required`, `source_definition_id` (FK CASCADE), `target_definition_id` (FK
**RESTRICT**), `org_id`, `lineage_id` (037), timestamps. Unique
`(source_definition_id, slug)`.

#### ce_* / cej_* (dynamic tables)
Physical tables created on demand by `SchemaManager`. Names are generated from the
catalog id's hex, **not** the slug (`services/api/src/api/services/identifiers.py`):

- `ce_<hex32>` — entity table (from `entity_definitions.id`)
- `cej_<hex32>` — M2M join table (from `entity_relationships.id`)
- `r_<hex32>` — relationship FK column

Each `ce_*` table carries `id`, `org_id` (NOT NULL, RLS scope), `created_at`,
`updated_at`, plus one column per field. `SchemaManager._apply_rls` applies the same
`tenant_isolation_*` policies **and** the `admin_bypass_all` policy (see
[Row-Level Security](#row-level-security)) and grants CRUD to `app_user`. Deleting a
definition drops its `ce_*` table (`DROP TABLE IF EXISTS`). Per-record write and
per-field read policies (`write_access` / `read_access`) are enforced in the repo
layer, not by RLS — see [Entity Access Control](#entity-access-control).

### Forms & Views

#### forms
Intake form definitions (migration 011). `id`, `name`, `slug` (unique
`(org_id, slug)`), `description`, `entity_definition_id` (FK CASCADE), `config`
JSONB (the element tree), `is_active`, `org_id`, `lineage_id` (037), timestamps.

#### form_links
Single-use, token-linked form invitations (migration 011).

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| form_id | UUID | FK `forms` (CASCADE) |
| target_record_id | UUID | Optional record to update |
| token_hash | VARCHAR(64) | SHA-256 of the raw token; **globally unique index** for public lookup |
| status | VARCHAR(12) | CHECK `pending`/`submitted`/`expired`/`revoked` |
| recipient_email | VARCHAR(320) | |
| expires_at / submitted_at | TIMESTAMPTZ | |
| created_by_id | UUID | FK `user_profiles` (SET NULL) |
| org_id | UUID | FK `orgs` (CASCADE); RLS scope |
| created_at / updated_at | TIMESTAMPTZ | |

The token is resolved (and thus the org discovered) before any tenant context
exists, so `ix_form_links_token_hash` is a global unique index resolved on a
privileged/bypass session.

#### views
Composable screens rendered by the shared form renderer (migration 021). `id`,
`name`, `slug` (unique `(org_id, slug)`), `description`,
`entity_definition_id` (FK CASCADE, **nullable** — a view may bind to an entity or
stand alone as a dashboard), `config` JSONB, `is_active`, `org_id`, `lineage_id`
(037), timestamps. See `[FORMS_AND_VIEWS.md](FORMS_AND_VIEWS.md)`.

### Workflows, Connections, Inbound & Outbox

The workflow engine (migration 009, extended by 012–014, 018, 023, 024) is a
BPMN-style token engine. `workflows`/`workflow_versions` are static, RLS-scoped;
`workflow_outbox`/`workflow_runs`/`workflow_run_steps`/`workflow_run_tokens` are
**RANGE-partitioned by `created_at`** with month partitions and a DEFAULT partition.
See `[WORKFLOW_ENGINE.md](WORKFLOW_ENGINE.md)`.

#### workflows
| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| name | VARCHAR(200) | Unique `(org_id, name)` |
| description | TEXT | |
| entity_definition_id | UUID | FK `entity_definitions` (CASCADE), **nullable** (migration 023 — manual/none-start workflows) |
| enabled | BOOLEAN | Default false |
| active_version_id | UUID | Published version (no FK constraint) |
| run_permission | JSONB | `{"mode": "org_admin"\|"any_member"\|"specific_roles", "role_ids": [...]}` (migration 012) |
| run_inline_on_change | BOOLEAN | Run inline in the mutating request vs. beat sweep (migration 024) |
| org_id | UUID | FK `orgs` (CASCADE); RLS scope |
| lineage_id | UUID | Migration 037 |
| created_at / updated_at | TIMESTAMPTZ | |

#### workflow_versions
Immutable published versions. `id`, `workflow_id` (FK CASCADE), `version_number`
(unique `(workflow_id, version_number)`), `status` (CHECK
`draft`/`published`/`archived`), `definition` JSONB, `published_at`,
`created_by_id`/`published_by_id` (FK SET NULL), `org_id`, timestamps. A BEFORE
UPDATE trigger (`workflow_versions_immutable`) rejects edits to a published
version's `definition`/`version_number`/`workflow_id` — only `status` may change.

#### workflow_outbox (partitioned)
Change events driving triggers (at-least-once). Key columns: `id`+`created_at`
(composite PK / partition key), `seq` BIGINT identity, `org_id` (FK CASCADE),
`entity_definition_id`, `entity_table`, `record_id`, `operation` (CHECK
create/update/delete), `before_data`/`after_data` JSONB, `actor_user_id`,
`origin_run_id`, `dedup_key`, `status` (CHECK pending/claimed/done/skipped),
`claimed_at`, `attempts`, and `source` (migration 013; CHECK widened to
`record`/`form`/`webhook` in migration 018).

#### workflow_runs (partitioned)
One execution per outbox event (or manual run). Key columns include `workflow_id`,
`workflow_version_id`, `outbox_id`, `outbox_seq`, `trigger_operation`, `record_id`,
`status` (CHECK pending/running/**waiting**/succeeded/failed/skipped — `waiting`
added in migration 014), `conditions_matched`, `input_snapshot`, `error`,
`started_at`/`finished_at`, `depth`. Migration 014 adds `resume_at` /
`resume_node_id` (parked delay runs). Migration 018 adds token-engine state:
`variables` JSONB, `step_seq`, `parent_run_id`, `parent_token_id`, `dead_letter`.
Unique `(org_id, outbox_id, workflow_id, created_at)`.

#### workflow_run_steps (partitioned)
One row per executed node. Columns include `run_id`, `run_created_at`, `node_id`,
`action_id`, `action_type`, `step_index` (widened smallint→int in migration 018),
`status` (CHECK pending/running/succeeded/failed/skipped/retrying), `attempts`,
`max_attempts` (default 3), `next_retry_at`, `input`/`output` JSONB, `error`,
`started_at`/`finished_at`, `celery_task_id`, and `token_id` (branch attribution;
migration 018).

#### workflow_run_tokens (partitioned) — migration 018
Durable BPMN control-flow tokens. RANGE-partitioned by `created_at`; folded into
`workflow_ensure_partitions`. Key columns: `run_id`, `node_id`,
`arrived_from_node_id`, `arrived_via_handle`, `status` (CHECK
active/running/waiting/completed/dead), `wait_kind`, `resume_at`,
`correlation_key`, `parent_token_id`, `created_by_node`, `depth`, `lease_owner`,
`leased_at`, `data` JSONB, `started_at`/`finished_at`. Several partial indexes drive
the token sweeps (see [Indexes](#indexes)).

#### workflow_connections — migration 019
Org-scoped connector credentials. `id`, `name` (unique `(org_id, name)`), `kind`
(default `http`), `base_url`, `auth_type` (default `none`), `secret_encrypted`
(Fernet, `ORG_ENCRYPTION_KEY`), `config` JSONB, `org_id`, `lineage_id` (037),
timestamps.

#### workflow_inbound_endpoints — migration 020
Public webhook URLs that start workflow runs. `id`, `name`, `token_hash`
VARCHAR(64) (**globally unique** — resolves the org before tenant context),
`workflow_id`, `enabled`, `signing_secret_encrypted` (Fernet HMAC secret; migration
022, NULL = token-only auth), `org_id`, `lineage_id` (037), timestamps. Indexed on
`token_hash`, `org_id`, `workflow_id`. See `[WORKFLOW_ENGINE.md](WORKFLOW_ENGINE.md)`
for signed-webhook verification.

### Reports

#### reports — migration 026
Saved aggregation query + visualization. RLS-scoped like every tenant table.

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| name / slug / description | — | Unique `(org_id, slug)` |
| entity_definition_id | UUID | FK `entity_definitions` (CASCADE); entity aggregated over |
| query | JSONB | `AggregateQuery` (group_by / metrics / filters / having / order_by / limit) |
| viz | JSONB | `Visualization` (chart type, axes, series, number format) |
| is_active | BOOLEAN | Default true |
| org_id | UUID | FK `orgs` (CASCADE); RLS scope |
| lineage_id | UUID | Migration 037 |
| created_at / updated_at | TIMESTAMPTZ | |

### API Keys

#### api_keys — migration 028
Org-scoped credentials for the `/api/v1` surface. Only the hash of a key is stored.
See `[API.md](API.md)` and `[AUTHENTICATION.md](AUTHENTICATION.md)`.

| Column | Type | Notes |
|--------|------|-------|
| id | UUID | PK |
| name | VARCHAR(120) | Human label |
| key_prefix | VARCHAR(20) | Non-secret display prefix (e.g. `km2_AbC12`) |
| key_hash | VARCHAR(64) | SHA-256 hex of the full key; **globally unique** (serves the by-hash auth lookup — no separate index) |
| scopes | JSONB | Granted permission scopes (e.g. `["reports:run"]`) |
| created_by_profile_id | UUID | FK `user_profiles` (SET NULL) — audit |
| last_used_at / expires_at / revoked_at | TIMESTAMPTZ | Lifecycle (nullable) |
| org_id | UUID | FK `orgs` (CASCADE); RLS scope |
| created_at / updated_at | TIMESTAMPTZ | |

**Auth path:** a presented key is hashed and resolved by `key_hash` on a privileged
(bypass) session — cross-tenant, before the org is known — then all data work runs
on an org-scoped RLS session.

### Agents Domain

Migration 030 creates the whole agents subsystem in one dependency-ordered
migration; every table is org-scoped (RLS) with the hardened policy template. See
`[AGENT_ORG.md](AGENT_ORG.md)`.

| Table | Purpose | Notable columns |
|---|---|---|
| `agents` | Agent definitions | `name` (unique per org), `kind`, `persona`, `provider`, `model`, `params`, `supervisor_id` (self FK SET NULL), `enabled`, `grants` JSONB, `mcp_server_ids` JSONB, `workflow_allowlist` JSONB, `lineage_id` (037) |
| `mcp_servers` | MCP server configs | `name` (unique per org), `transport`, `command`, `url`, `config`, `secret_encrypted`, `enabled`, `lineage_id` (037); OAuth cols added in 032 |
| `work_orders` | Units of work | `slug` (unique per org), `title`, `status`, `body`, `priority`, `assigned_agent_id` (SET NULL), `created_by_profile_id`, `rolled_over_to_id` (self FK SET NULL) |
| `work_order_tasks` | Sub-tasks | `work_order_id` (CASCADE), `key`, `title`, `status`, `sort_order`, `assigned_agent_id` |
| `work_order_artifacts` | Outputs | `work_order_id` (CASCADE), `kind`, `document_id` (SET NULL), `filename`, `mime`, `size` |
| `agent_runs` | Agent executions | `agent_id`/`work_order_id`/`parent_run_id` (SET NULL), `status`, `trigger`, `wait_kind`, `provider`, `model`, `input`, `error`, `actor_user_id`, token counters, `cost_usd` NUMERIC(12,6), timing |
| `agent_run_steps` | Run steps | `run_id` (CASCADE), `seq`, `kind`, `name`, `content` JSONB, `tokens` |
| `agent_approvals` | Human-in-loop gates | `run_id` (CASCADE), `step_id` (SET NULL), `tool_name`, `arguments`, `status`, `decided_by_profile_id`, `decided_at` |
| `agent_notifications` | In-app/external notices | `kind`, `run_id`/`work_order_id`/`recipient_profile_id` (SET NULL), `recipient_role`, `title`, `body`, `status`, `delivered_channels` |
| `agent_schedules` | Cron triggers | `agent_id` (CASCADE), `cron`, `task`, `enabled`, `last_run_at`, `next_run_at` |
| `work_order_entries` | Work-order log/thread | `work_order_id` (CASCADE), `agent_run_id`/`agent_id` (SET NULL), `role`, `text` |

Per-org agent posture columns live on `orgs`: `agent_notify_workflow_id` (031),
`agent_autonomy` (033).

### MCP Servers & OAuth

Migration 032 adds OAuth 2.1 to `mcp_servers` and two tables. OAuth columns on
`mcp_servers`: `oauth_identity` (default `org`), `oauth_client_secret_encrypted`,
`oauth_access_token_encrypted`, `oauth_refresh_token_encrypted`,
`oauth_token_expires_at` (all Fernet-encrypted / nullable).

- `mcp_server_user_tokens` — per-user OAuth tokens: `mcp_server_id` (CASCADE),
  `user_profile_id` (CASCADE), `access_token_encrypted`, `refresh_token_encrypted`,
  `token_expires_at`, `org_id`; unique `(mcp_server_id, user_profile_id)`.
- `mcp_oauth_flows` — in-flight authorizations: `mcp_server_id` (CASCADE),
  `user_profile_id` (CASCADE, nullable), `state` (unique), `code_verifier` (PKCE),
  `redirect_uri`, `org_id`.

### LLM Provider Credentials

#### org_provider_credentials — migration 029
One row per `(org, provider)` holding a Fernet-encrypted provider API key. `id`,
`provider` VARCHAR(40), `secret_encrypted` (never plaintext), `org_id`, timestamps;
unique `(org_id, provider)`. The resolver prefers this over the central key in
settings.

### Config Lineage & Release Management

Change management adds durable cross-environment identity and release promotion.
See `[CHANGE_MANAGEMENT.md](CHANGE_MANAGEMENT.md)`.

#### Config lineage — migration 037
Adds a nullable `lineage_id` UUID to the **14 config tables** that participate in
org portability/promotion: `entity_definitions`, `entity_fields`,
`entity_relationships`, `folders`, `tags`, `documents`, `forms`, `views`, `reports`,
`workflows`, `workflow_connections`, `workflow_inbound_endpoints`, `mcp_servers`,
`agents`. Semantics live in the exporter/importer, not the DB: `lineage_id IS NULL`
means **self-origin** (identity is the row's own `id`) — so existing rows need zero
backfill. Each table gets a **partial unique index** `uq_<table>_lineage` on
`(org_id, lineage_id) WHERE lineage_id IS NOT NULL`, so a promotion re-targets
exactly one row per lineage and self-origin rows cost nothing.

#### Release management — migration 038
Five tenant-scoped tables, all owned by the SOURCE org, all under the hardened
FORCE-RLS template **plus** the `admin_bypass_all` policy (so the site-admin
cross-org deployment log can read them on a bypass session). Status columns are
`varchar` + CHECK, never Postgres enums.

| Table | Purpose | Notable columns |
|---|---|---|
| `promotion_targets` | Where a release can be promoted | `name` (unique per org), `kind` (CHECK `local_org`/`remote_instance`), `enabled`, `target_org_id` (local), `base_url`/`remote_org_id`/`secret_encrypted` (remote, Fernet, never exported), `config`; shape CHECK enforces the local-vs-remote column set |
| `releases` | Immutable frozen snapshot + governance | `name` (unique per org), `status` (CHECK draft/in_review/approved/rejected/archived), `bundle` JSONB **or** `bundle_uri`+`bundle_hash`+`bundle_format_version` (exactly one for a frozen release), `created_by_id`, `submitted_at`, `approved_at` |
| `release_items` | Normalized inventory of a release | `release_id` (CASCADE), `object_type`, `lineage_id`, `source_object_id`, `natural_key`, `fingerprint`; unique `(release_id, object_type, lineage_id)` |
| `release_approvals` | Append-only approval audit | `release_id` (CASCADE), `approver_id` (SET NULL), `decision` (CHECK approved/rejected), `comment`; unique `(release_id, approver_id)` |
| `release_promotions` | One execution vs. one target | `release_id` (CASCADE), `target_id` (SET NULL), denormalized `target_kind`/`target_label`/`target_org_id`, `status` (CHECK pending/promoting/promoted/failed/rolled_back), `strategy`, `dry_run`, `result_summary`, reverse snapshot (`pre_state_bundle`/`pre_state_uri`/`pre_state_hash`), `rollback_source_id` (self FK SET NULL), rollback/timing columns |

### Entity Access Control

Migration 039 adds two catalog policy columns making record surfaces tamper-proof:

- `entity_definitions.write_access` — `member` (default: any member may write via the
  record API) or `workflow_only` (only the workflow engine and org admins may write;
  direct member writes are 403'd).
- `entity_fields.read_access` — `member` (default) or `server_only` (values stripped
  from the record API for regular members and not filterable/sortable/groupable; only
  the workflow engine and org admins see them).

Both default to the pre-existing fully-open behavior. Enforcement is in the
`DynamicEntityRepository` layer via a `privileged` flag (workflows + admins bypass),
not in RLS.

## Row-Level Security

Every tenant-scoped table has RLS **enabled and FORCE**d, so even the table owner is
subject to policy (a plain `ENABLE` would let the owner bypass RLS). Each table gets
four `tenant_isolation_*` policies plus, since migration 034, one `admin_bypass_all`
policy.

### Tenant isolation policies

```sql
CREATE POLICY tenant_isolation_select ON <table>
  FOR SELECT USING (org_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid);
CREATE POLICY tenant_isolation_insert ON <table>
  FOR INSERT WITH CHECK (org_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid);
CREATE POLICY tenant_isolation_update ON <table>
  FOR UPDATE USING (org_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid);
CREATE POLICY tenant_isolation_delete ON <table>
  FOR DELETE USING (org_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid);
```

**RED-3 hardening (migration 002):** the tenant GUC is normalised with
`nullif(current_setting(...), '')` before the `::uuid` cast. On a pooled connection a
set-then-reverted GUC reads back as the empty string `''` (not NULL); a bare
`''::uuid` cast raises `invalid input syntax for type uuid`, turning a benign
"no tenant context" state into an error on the next RLS query. `nullif('', '')`
normalises to NULL, so an unset/empty tenant deterministically returns zero rows and
blocks all writes — **fail closed and error-free**. Migration 001 created the base
tables with the un-hardened predicate; migration 002 rewrote them; every table added
from migration 008 onward uses the hardened predicate directly. A *non-empty*
malformed tenant value still raises on the cast (fail-loud) — acceptable, since the
tenant id is always a validated UUID from the request context.

### Cross-org bypass policy (migration 034)

KM2's privileged, cross-org / no-tenant-context paths (`get_db`, the workflow and
agent poll sweeps' cross-org claims, provisioning, site-admin, token→org lookups)
historically relied on a superuser/BYPASSRLS connection role. That cannot run on
Google Cloud SQL, whose `cloudsqlsuperuser` cannot hold `BYPASSRLS`. Migration 034
adopts the **GUC-gated permissive policy** pattern instead:

```sql
CREATE POLICY admin_bypass_all ON <table>
  USING (current_setting('app.bypass', true) = 'on')
  WITH CHECK (current_setting('app.bypass', true) = 'on');
```

RLS policies are OR-combined (permissive), so a row is visible/writable when *either*
the tenant policy matches *or* the caller has opted into `app.bypass = 'on'`. Normal
request paths never set the GUC, so RLS still fully isolates them. The bypass is
deliberately read-AND-write across all commands because the sweep claims work across
every org in a single `UPDATE … FOR UPDATE SKIP LOCKED`. Migration 034 is
catalog-driven (covers every current FORCE-RLS table). **Convention:** any future
migration adding a FORCE-RLS table MUST also add its `admin_bypass_all` policy
(migration 038 does; `ce_*` tables get theirs from `SchemaManager._apply_rls`).

Migration 034 also makes `workflow_ensure_partitions(int)` `SECURITY DEFINER` (with a
pinned `search_path`) so the non-owner `km_app` role can still create partitions.

## Database Roles & Sessions

The app connects as **`km_app`** — a non-superuser, non-`BYPASSRLS` login role that is
a member of `app_user` — so RLS is genuinely enforced. Session mode is chosen
transactionally with `SET LOCAL` / `set_config(..., true)` in
`services/api/src/api/db_scope.py`, wired to FastAPI dependencies in
`services/api/src/api/dependencies.py`.

| Mode | Helper (`db_scope.py`) | Dependency | What it does |
|---|---|---|---|
| Tenant-scoped | `enter_tenant` | `get_tenant_db` | `SET LOCAL ROLE app_user`; `app.bypass='off'`; pin `app.current_tenant_id` |
| Tenant + DDL | `enter_tenant_owner` | — | stays on `km_app` (keeps `CREATE`) for `ce_*` authoring; still RLS-scoped by GUC |
| Cross-org bypass | `enter_bypass` / `exit_to_bypass` | `get_db` | `SET LOCAL app.bypass='on'` (honored by `admin_bypass_all`) |

Because everything uses `SET LOCAL`, mode auto-reverts on commit/rollback and pooled
connections stay clean. A session that sets *neither* tenant nor bypass fails closed
(RLS with no tenant GUC returns zero rows).

| Role | Attributes | Purpose |
|---|---|---|
| `km_app` | LOGIN, non-superuser, non-BYPASSRLS, member of `app_user`, `CREATE` on `public` | Runtime app connection (dev: `docker/init-db.sql`; prod: Terraform + migration 035) |
| `app_user` | NOSUPERUSER, NOBYPASSRLS, per-table CRUD grants | Target of `SET LOCAL ROLE app_user`; RLS enforced (migration 007) |
| `app_admin` | BYPASSRLS | Legacy admin/migration role in `docker/init-db.sql`; superseded for app paths by the GUC bypass |
| `POSTGRES_USER` (e.g. `redarch`) | superuser, schema owner | Runs migrations; owns the tables/functions |

**Adding a new tenant table:** grant `app_user` DML in the same migration
(`GRANT SELECT, INSERT, UPDATE, DELETE ON <table> TO app_user`) or tenant requests
fail once they `SET ROLE app_user`, and add the `admin_bypass_all` policy. Migration
007 grants `ALL TABLES IN SCHEMA public` at its point in history, but that does not
cover tables created by later migrations. See `[AUTHENTICATION.md](AUTHENTICATION.md)`
for how the Clerk JWT resolves to a tenant context, and `[RBAC.md](RBAC.md)` for
in-org authorization.

## Migrations

Managed with Alembic (`services/api/alembic/`). The repo drives them via `make`:

```bash
# Apply all migrations (uses DATABASE_URL_LOCAL as the admin POSTGRES_USER on :5433)
make migrate

# Create a new migration
make migration MSG="description"      # alembic revision --autogenerate

# Or directly
cd services/api
DATABASE_URL=<admin-url> uv run alembic upgrade head
DATABASE_URL=<admin-url> uv run alembic downgrade -1
```

Migrations run as the schema-owning admin role, not `km_app`.

### Migration reference (001 → 039)

| Rev | File | Change |
|---|---|---|
| 001 | `001_initial_schema_with_rls.py` | Core tables + RLS policies |
| 002 | `002_harden_rls_nullif.py` | RED-3 fail-closed RLS (`nullif(..., '')`) |
| 003 | `003_rename_keycloak_sub_to_auth_subject.py` | `keycloak_sub` → `auth_subject` |
| 004 | `004_add_user_is_active.py` | `user_profiles.is_active` |
| 005 | `005_add_document_size_bytes.py` | `documents.size_bytes` |
| 006 | `006_chat_sessions_user_cascade.py` | `chat_sessions.user_id` NOT NULL + CASCADE |
| 007 | `007_ensure_app_user_role.py` | Create `app_user` (NOSUPERUSER/NOBYPASSRLS) + grants |
| 008 | `008_custom_entities_catalog.py` | `entity_definitions`, `entity_fields`, `entity_relationships` |
| 009 | `009_workflow_engine.py` | Workflows + partitioned outbox/runs/steps, immutability trigger, `workflow_ensure_partitions` |
| 010 | `010_enable_pg_trgm.py` | Enable `pg_trgm` |
| 011 | `011_intake_forms.py` | `forms`, `form_links` (global token_hash index) |
| 012 | `012_workflow_run_permission.py` | `workflows.run_permission` |
| 013 | `013_workflow_outbox_source.py` | `workflow_outbox.source` |
| 014 | `014_workflow_run_delay.py` | Delay/wait: `resume_at`, `resume_node_id`, `waiting` status |
| 015 | `015_add_document_permissions.py` | Per-document permission mask/config columns |
| 016 | `016_encrypt_org_openai_key.py` | Encrypt `orgs.openai_api_key` (Fernet, `ORG_ENCRYPTION_KEY`) |
| 017 | `017_add_document_celery_task_id.py` | `documents.celery_task_id` |
| 018 | `018_workflow_token_engine.py` | `workflow_run_tokens` (partitioned) + run/step token columns |
| 019 | `019_workflow_connections.py` | `workflow_connections` (encrypted secrets) |
| 020 | `020_workflow_inbound_endpoints.py` | `workflow_inbound_endpoints` |
| 021 | `021_views.py` | `views` |
| 022 | `022_inbound_signing_secret.py` | `workflow_inbound_endpoints.signing_secret_encrypted` |
| 023 | `023_nullable_workflow_entity.py` | `workflows.entity_definition_id` nullable |
| 024 | `024_workflow_run_inline_on_change.py` | `workflows.run_inline_on_change` |
| 025 | `025_filterable_btree_indexes.py` | Backfill filterable btree indexes (CONCURRENTLY) |
| 026 | `026_reports.py` | `reports` |
| 027 | `027_workflows_inline_partial_index.py` | Partial index for inline dispatch |
| 028 | `028_api_keys.py` | `api_keys` |
| 029 | `029_org_provider_credentials.py` | `org_provider_credentials` |
| 030 | `030_agents_domain.py` | Agents subsystem (11 tables) |
| 031 | `031_org_agent_notify_workflow.py` | `orgs.agent_notify_workflow_id` |
| 032 | `032_mcp_oauth.py` | MCP OAuth 2.1 tables + `mcp_servers` columns |
| 033 | `033_org_agent_autonomy.py` | `orgs.agent_autonomy` |
| 034 | `034_admin_bypass_policies.py` | `admin_bypass_all` GUC-gated policy on every FORCE-RLS table |
| 035 | `035_grant_km_app_role.py` | Grant `km_app` its `app_user` membership + schema CREATE |
| 036 | `036_org_home_view.py` | `orgs.home_view_id` |
| 037 | `037_config_lineage.py` | `lineage_id` on 14 config tables |
| 038 | `038_release_management.py` | Release/promotion tables (5) |
| 039 | `039_entity_access_control.py` | `write_access` / `read_access` catalog columns |

> The `services/api-go` Go rewrite maintains its own migration set under
> `services/api-go/migrations/` (the Python schema above is authoritative today).

### Partition maintenance

`workflow_ensure_partitions(months_ahead)` (defined in migration 009, extended in 018,
made `SECURITY DEFINER` in 034) pre-creates month partitions for `workflow_outbox`,
`workflow_runs`, `workflow_run_steps`, `workflow_run_tokens`. A Celery beat job calls
it; trigger manually via `POST /api/internal/workflows/maintain-partitions`.

## Indexes

Beyond the FK/unique indexes noted per table:

| Table | Index | Columns / predicate | Purpose |
|---|---|---|---|
| documents | ix_documents_folder_id / _uploaded_by_id | folder_id / uploaded_by_id | FK lookups |
| folders | ix_folders_dot_path | dot_path | Path queries |
| user_profiles | (unique) auth_subject | auth_subject | Clerk subject lookup |
| form_links | ix_form_links_token_hash | token_hash (unique, global) | Pre-tenant token resolution |
| workflow_inbound_endpoints | ix_…_token_hash | token_hash (unique, global) | Pre-tenant webhook resolution |
| workflow_outbox | ix_wf_outbox_pending | seq WHERE status='pending' | Claim ordering (Merge Append, no sort) |
| workflow_outbox | ix_wf_outbox_entity | (org_id, entity_definition_id) WHERE status='pending' | Trigger lookup |
| workflow_runs | ix_wf_runs_workflow | (org_id, workflow_id, created_at) | Run history |
| workflow_runs | ix_wf_runs_waiting | resume_at WHERE status='waiting' | Delay-run sweep (migration 014) |
| workflow_run_steps | ix_wf_steps_run | (org_id, run_id, step_index) | Step ordering |
| workflow_run_tokens | ix_wf_tokens_active / _timer / _correlation / _stuck / _run | partial (see migration 018) | Token engine sweeps |
| workflows | ix_workflows_inline_entity | (org_id, entity_definition_id) WHERE enabled AND run_inline_on_change | Inline-dispatch hot path (migration 027) |
| 14 config tables | uq_<table>_lineage | (org_id, lineage_id) WHERE lineage_id IS NOT NULL | Promotion re-target (migration 037) |

### Dynamic `ce_*` entity indexes

Each `ce_*` table (created by `SchemaManager`) carries:

- a **keyset** btree on `(created_at DESC, id DESC)` for the records grid,
- a **trigram GIN** index (`gin_trgm_ops`, needs `pg_trgm` from migration 010) per
  text/long-text/picklist field for substring search,
- a **filterable btree** `(org_id, col DESC, id DESC)` per field in
  `FILTERABLE_FIELD_TYPES` (integer/bigint/numeric/date/timestamptz/uuid/picklist),
  serving equality/range filters, GROUP BY, and keyset pagination under a custom sort.

Migration 025 backfills the filterable btree indexes for pre-existing entity tables,
built `CONCURRENTLY` in an autocommit block to avoid table-wide write locks. Index
names are derived deterministically from the field/definition id
(`services/api/src/api/services/identifiers.py`) so runtime DDL and the backfill never
collide.

## Cascade Behavior

Deleting an org cascades to all tenant-scoped data (every tenant table's `org_id` FK
is `ON DELETE CASCADE`). Notable chains:

- `orgs` → regions/departments/roles/groups → `membership_*`
- `orgs` → `folders` → `documents` (`folder_id` SET NULL on folder delete)
- `orgs` → `tags` → `document_tags`; `orgs` → `chat_sessions`
- `orgs` → `entity_definitions` → `entity_fields`, `entity_relationships`
  (`target_definition_id` is **RESTRICT** — can't delete an entity another relationship
  targets) → the physical `ce_*` table is dropped (`DROP TABLE IF EXISTS`)
- `orgs` → `workflows` → `workflow_versions`; partitioned outbox/runs/steps/tokens
- `orgs` → `forms` → `form_links`; `orgs` → `views`, `reports`, `api_keys`
- `orgs` → agents domain (`work_orders` → tasks/artifacts/entries; `agent_runs` →
  steps/approvals)
- `orgs` → release tables; `releases` → items/approvals/promotions
- `user_profiles` → `user_org_memberships`, `chat_sessions`, `mcp_server_user_tokens`
  (CASCADE); audit FKs (`created_by_*`, `approver_id`, …) are SET NULL

### Partition retention

The RANGE-partitioned workflow tables retain partitions indefinitely (no TTL). For
long-term retention, drop old month partitions manually:

```sql
DROP TABLE IF EXISTS workflow_outbox_202412, workflow_runs_202412, ... CASCADE;
```

### Brain-API cascade

Org deletion also triggers best-effort external cleanup (via an API → brain-api HTTP
call, logged but non-blocking): Qdrant collections (`{tenant_id}_chunks`,
`{tenant_id}_documents`) and Neo4j nodes with the matching tenant label. See
`[KNOWLEDGE_ENGINE.md](KNOWLEDGE_ENGINE.md)`.

## Backup & Recovery

```bash
# Full logical backup (run as the admin POSTGRES_USER)
pg_dump -h localhost -p 5433 -U redarch -d redarch_km > backup.sql

# Restore
psql -h localhost -p 5433 -U redarch -d redarch_km < backup.sql
```

For production, use point-in-time recovery with WAL archiving. Encrypted columns
(`orgs.openai_api_key`, `*_secret_encrypted`, `*_token_encrypted`) are dumped as
ciphertext and require the same `ORG_ENCRYPTION_KEY` to decrypt after restore — back
up the key separately. Object-store bundles referenced by `releases.bundle_uri` /
`release_promotions.pre_state_uri` live in MinIO/GCS, not Postgres; back those up too.

## Known gaps / TODO

- `AUTHENTICATION.md` and `CHANGE_MANAGEMENT.md` are cross-linked above; confirm they
  exist in `docs/` (they are part of the in-progress documentation set and may land
  separately). `RBAC.md`, `WORKFLOW_ENGINE.md`, `API.md`, `ARCHITECTURE.md`,
  `AGENT_ORG.md`, `FORMS_AND_VIEWS.md`, and `KNOWLEDGE_ENGINE.md` are present.
- Column lists here name the notable/load-bearing columns per table; for exhaustive
  definitions and constraints, the migration files in
  `services/api/alembic/versions/` are authoritative.

> Reviewed 2026-07-16 against Alembic migration 039. Source of truth is the code; if this doc disagrees with the code, the code wins.
