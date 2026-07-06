# Database

Red Arch Knowledge Manager uses PostgreSQL 18 with Row-Level Security (RLS) for multi-tenant data isolation.

## Connection

```bash
# Default connection string
postgresql+asyncpg://redarch:${POSTGRES_PASSWORD}@postgres:5432/redarch_km

# Host port mapping (dev): 5433 -> 5432
psql -h localhost -p 5433 -U redarch -d redarch_km
```

## Schema Overview

### Core Tables

```
┌─────────────────┐
│      orgs       │  Multi-tenant root
└────────┬────────┘
         │
    ┌────┴────┬──────────────┬──────────────┐
    │         │              │              │
┌───▼───┐ ┌───▼───┐    ┌─────▼─────┐ ┌──────▼──────┐
│regions│ │depts  │    │  folders  │ │user_profiles│
└───────┘ └───────┘    └─────┬─────┘ └──────┬──────┘
    │         │              │              │
┌───▼───┐ ┌───▼───┐    ┌─────▼─────┐ ┌──────▼──────┐
│ roles │ │groups │    │ documents │ │  user_org_  │
└───────┘ └───────┘    └───────────┘ │ memberships │
                                     └─────────────┘
```

### Table Definitions

#### orgs
Multi-tenant organization root.

| Column | Type | Description |
|--------|------|-------------|
| id | UUID | Primary key |
| name | VARCHAR(200) | Unique org name |
| description | TEXT | Optional description |
| use_knowledge_graph | BOOLEAN | Enable Neo4j graph features |
| openai_api_key | VARCHAR(800) | Optional per-org OpenAI key |
| permission_number | SMALLINT | Org-level permission slot |
| created_at | TIMESTAMPTZ | Creation timestamp |
| updated_at | TIMESTAMPTZ | Last update timestamp |

#### user_profiles
User identities synchronized from Clerk.

| Column | Type | Description |
|--------|------|-------------|
| id | UUID | Primary key |
| auth_subject | VARCHAR(255) | Clerk subject ID (unique; renamed from keycloak_sub in migration 003) |
| username | VARCHAR(150) | Unique username |
| email | VARCHAR(254) | Unique email |
| description | TEXT | Profile description |
| is_site_admin | BOOLEAN | Platform-wide admin flag |
| is_active | BOOLEAN | Default true; deactivated users are rejected at auth time (403) even with a valid Clerk JWT (migration 004) |
| created_at | TIMESTAMPTZ | Creation timestamp |
| updated_at | TIMESTAMPTZ | Last update timestamp |

> **RLS role split (tenant vs cross-org sessions):** `user_org_memberships`
> and the other tenant tables have FORCE ROW LEVEL SECURITY. Tenant-scoped
> requests go through `get_tenant_db`, which runs `SET LOCAL ROLE app_user`
> (dropping off the privileged connection role so RLS is actually enforced) and
> sets `app.current_tenant_id`. Cross-org queries — the site-admin console
> (`GET /api/admin/users/{id}/memberships`), `/users/me`, and `require_org_access`'s
> membership lookup — go through `get_db`, which stays on the privileged
> connection role (superuser/BYPASSRLS, as in the shipped compose files) and
> deliberately bypasses RLS so those rows are visible without a tenant context.
> Application code also filters every tenant-scoped query by `org_id` explicitly
> (tenant-bound repositories), so isolation holds even if RLS is off on a given
> connection — defense in depth, not RLS alone.

#### user_org_memberships
Links users to organizations with role assignments.

| Column | Type | Description |
|--------|------|-------------|
| id | UUID | Primary key |
| profile_id | UUID | FK to user_profiles |
| org_id | UUID | FK to orgs |
| is_org_admin | BOOLEAN | Org-level admin flag |
| created_at | TIMESTAMPTZ | Creation timestamp |
| updated_at | TIMESTAMPTZ | Last update timestamp |

**Unique constraint:** (profile_id, org_id)

#### Permission Dimension Tables

Four tables define permission dimensions within an org:
- `regions` — Geographic regions
- `departments` — Business departments
- `roles` — Job roles
- `groups` — Custom groups

Each has the same structure:

| Column | Type | Description |
|--------|------|-------------|
| id | UUID | Primary key |
| name | VARCHAR(255) | Dimension name |
| description | TEXT | Optional description |
| permission_number | SMALLINT | Slot number (0-31) |
| org_id | UUID | FK to orgs |

#### Membership Junction Tables

Link memberships to dimensions:
- `membership_regions`
- `membership_departments`
- `membership_roles`
- `membership_groups`

Each has composite PK (membership_id, {dimension}_id).

#### folders
Hierarchical document organization with permission masks.

| Column | Type | Description |
|--------|------|-------------|
| id | UUID | Primary key |
| name | VARCHAR(255) | Folder name |
| description | TEXT | Optional description |
| order | INTEGER | Display order |
| dot_path | TEXT | Materialized path (e.g., "a.b.c") |
| view_permission_masks | BIGINT[] | Access masks for viewers |
| contributor_permission_masks | BIGINT[] | Access masks for contributors |
| viewer_permissions_config | JSONB | Human-readable permission config |
| contributor_permissions_config | JSONB | Human-readable permission config |
| org_id | UUID | FK to orgs |
| parent_id | UUID | FK to self (nullable) |

**Unique constraint:** (org_id, name, parent_id)

#### documents
Knowledge base documents.

| Column | Type | Description |
|--------|------|-------------|
| id | UUID | Primary key |
| title | VARCHAR(255) | Document title |
| description | TEXT | Optional description |
| text | TEXT | Document content |
| document_key | VARCHAR(255) | Unique key per org |
| document_url | VARCHAR(2048) | External URL |
| processing_status | VARCHAR(20) | PENDING/PROCESSING/COMPLETE/ERROR/STOPPED/DELETED |
| processing_details | JSONB | Processing metadata |
| metadata | JSONB | Custom attributes |
| use_knowledge_graph | BOOLEAN | Override org default |
| org_id | UUID | FK to orgs |
| folder_id | UUID | FK to folders |
| uploaded_by_id | UUID | FK to user_profiles |

#### tags
Document classification tags.

| Column | Type | Description |
|--------|------|-------------|
| id | UUID | Primary key |
| name | VARCHAR(100) | Tag name |
| org_id | UUID | FK to orgs |

**Unique constraint:** (org_id, name)

#### document_tags
Many-to-many linking documents to tags.

| Column | Type | Description |
|--------|------|-------------|
| document_id | UUID | FK to documents |
| tag_id | UUID | FK to tags |

**Primary key:** (document_id, tag_id)

#### chat_sessions
User chat history.

| Column | Type | Description |
|--------|------|-------------|
| id | UUID | Primary key |
| chat_data | JSONB | Message history |
| deleted | BOOLEAN | Soft delete flag |
| org_id | UUID | FK to orgs |
| user_id | UUID | FK to user_profiles |

### Custom Entities

#### entity_definitions
Org-defined entity schemas. Each definition has a corresponding dynamically-created `ce_<slug>` physical table.

| Column | Type | Description |
|--------|------|-------------|
| id | UUID | Primary key |
| name | VARCHAR(200) | Human-readable name |
| slug | VARCHAR(63) | Unique slug per org; identifier for the ce_* table |
| description | TEXT | Optional description |
| is_active | BOOLEAN | Soft-delete flag |
| org_id | UUID | FK to orgs |
| created_at | TIMESTAMPTZ | Creation timestamp |
| updated_at | TIMESTAMPTZ | Last update timestamp |

**Unique constraint:** (org_id, slug)

#### entity_fields
Field definitions for entities. Types: text, integer, decimal, boolean, date, datetime, single_select, multi_select.

| Column | Type | Description |
|--------|------|-------------|
| id | UUID | Primary key |
| entity_definition_id | UUID | FK to entity_definitions |
| name | VARCHAR(200) | Field name |
| slug | VARCHAR(63) | Column name in the ce_* table |
| field_type | VARCHAR(32) | text, integer, decimal, boolean, date, datetime, single_select, multi_select |
| is_required | BOOLEAN | NOT NULL constraint |
| metadata | JSONB | Field-type-specific config (e.g., enum values for select) |
| org_id | UUID | FK to orgs |
| created_at | TIMESTAMPTZ | Creation timestamp |

#### entity_relationships
Relationships between entities (foreign keys).

| Column | Type | Description |
|--------|------|-------------|
| id | UUID | Primary key |
| source_definition_id | UUID | FK to entity_definitions (source entity) |
| target_definition_id | UUID | FK to entity_definitions (target entity) |
| name | VARCHAR(200) | Relationship name (e.g., "belongs_to_company") |
| org_id | UUID | FK to orgs |
| created_at | TIMESTAMPTZ | Creation timestamp |

#### ce_* (Dynamic Entity Tables)
Physical Postgres tables created on-demand when an entity is defined. Table name is `ce_<slug>`. Columns match the entity_fields.

Example: For entity `"company"` with fields `["name", "industry"]`:
```sql
CREATE TABLE ce_company (
  id UUID PRIMARY KEY,
  name TEXT,
  industry TEXT,
  org_id UUID NOT NULL,  -- RLS scope
  created_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ
)
```

All ce_* tables have RLS policies (tenant_isolation_*) and are RLS-enabled with FORCE ROW LEVEL SECURITY.

### Workflows

#### workflows
Workflow definitions (automation rules).

| Column | Type | Description |
|--------|------|-------------|
| id | UUID | Primary key |
| name | VARCHAR(200) | Workflow name |
| description | TEXT | Optional description |
| entity_definition_id | UUID | FK to entity_definitions (trigger target) |
| enabled | BOOLEAN | Enable/disable flag |
| active_version_id | UUID | FK to workflow_versions (published version) |
| run_permission | JSONB | Who may manually run: `{ "mode": "org_admin" \| "any_member" \| "specific_roles", "role_ids": [...] }` |
| org_id | UUID | FK to orgs |
| created_at | TIMESTAMPTZ | Creation timestamp |
| updated_at | TIMESTAMPTZ | Last update timestamp |

**Unique constraint:** (org_id, name)

#### workflow_versions
Immutable versions of a workflow. Published versions are locked (only status may change).

| Column | Type | Description |
|--------|------|-------------|
| id | UUID | Primary key |
| workflow_id | UUID | FK to workflows |
| version_number | INTEGER | Incrementing version |
| status | VARCHAR(20) | draft, published, archived |
| definition | JSONB | Workflow definition (nodes, edges, triggers, conditions, actions) |
| published_at | TIMESTAMPTZ | When status became published |
| created_by_id | UUID | FK to user_profiles |
| published_by_id | UUID | FK to user_profiles |
| org_id | UUID | FK to orgs |
| created_at | TIMESTAMPTZ | Creation timestamp |
| updated_at | TIMESTAMPTZ | Last update timestamp |

**Unique constraint:** (workflow_id, version_number)

#### workflow_outbox
Change events for entities (insert, update, delete). Source for workflow triggering (at-least-once semantics).
RANGE-partitioned by `created_at` with month boundaries.

| Column | Type | Description |
|--------|------|-------------|
| id | UUID | Partition key + primary key |
| created_at | TIMESTAMPTZ | Partition key (RANGE); also event timestamp |
| seq | BIGINT | Global sequence for claim ordering |
| org_id | UUID | FK to orgs |
| entity_definition_id | UUID | ID of the entity that changed |
| entity_table | VARCHAR(63) | Physical table name (ce_*) |
| record_id | UUID | ID of the record that changed |
| operation | VARCHAR(10) | create, update, delete |
| before_data | JSONB | Row state before (update/delete) |
| after_data | JSONB | Row state after (create/update) |
| actor_user_id | UUID | FK to user_profiles; who triggered the change |
| origin_run_id | UUID | If generated by a workflow action, the run_id |
| dedup_key | VARCHAR(128) | Optional idempotency key (deduplication) |
| status | VARCHAR(10) | pending, claimed, done, skipped |
| claimed_at | TIMESTAMPTZ | When the dispatcher claimed it |
| attempts | SMALLINT | Retry count |

**Partitioning:** RANGE (created_at) with month partitions; pre-created by `workflow_ensure_partitions(months_ahead)`.

#### workflow_runs
Workflow executions (one per outbox event).
RANGE-partitioned by `created_at` with month boundaries.

| Column | Type | Description |
|--------|------|-------------|
| id | UUID | Partition key + primary key |
| created_at | TIMESTAMPTZ | Partition key (RANGE) |
| org_id | UUID | FK to orgs |
| workflow_id | UUID | FK to workflows |
| workflow_version_id | UUID | FK to workflow_versions (published version used) |
| outbox_id | UUID | FK to workflow_outbox (change event) |
| outbox_seq | BIGINT | Sequence number from outbox |
| trigger_operation | VARCHAR(10) | create, update, delete (what triggered the run) |
| record_id | UUID | ID of the record being processed |
| status | VARCHAR(12) | pending, running, succeeded, failed, skipped |
| conditions_matched | BOOLEAN | Did all conditions pass? |
| input_snapshot | JSONB | Record data at execution time |
| error | TEXT | Error message (if status = failed) |
| started_at | TIMESTAMPTZ | Execution start time |
| finished_at | TIMESTAMPTZ | Execution end time |
| depth | SMALLINT | Recursion depth (0 for outbox-triggered; >0 for recursive runs) |

**Unique constraint:** (org_id, outbox_id, workflow_id, created_at)

#### workflow_run_steps
Individual step executions within a run (one per node: condition, action, etc.).
RANGE-partitioned by `created_at` with month boundaries.

| Column | Type | Description |
|--------|------|-------------|
| id | UUID | Partition key + primary key |
| created_at | TIMESTAMPTZ | Partition key (RANGE) |
| org_id | UUID | FK to orgs |
| run_id | UUID | FK to workflow_runs |
| run_created_at | TIMESTAMPTZ | Run's created_at (for partition pruning) |
| node_id | VARCHAR(64) | Node ID in the workflow definition |
| action_id | VARCHAR(64) | Optional action instance ID |
| action_type | VARCHAR(64) | Action type (update_record_field, send_email, send_webhook, etc.) |
| step_index | SMALLINT | Execution order |
| status | VARCHAR(12) | pending, running, succeeded, failed, skipped, retrying |
| attempts | SMALLINT | Retry count |
| max_attempts | SMALLINT | Max retries (default: 3) |
| next_retry_at | TIMESTAMPTZ | Scheduled retry time |
| input | JSONB | Step input (action params) |
| output | JSONB | Step output (action result) |
| error | TEXT | Error message (if status = failed) |
| started_at | TIMESTAMPTZ | Step start time |
| finished_at | TIMESTAMPTZ | Step end time |
| celery_task_id | VARCHAR(64) | Celery task ID (if async) |

### Forms (Intake)

#### forms
Intake form definitions.

| Column | Type | Description |
|--------|------|-------------|
| id | UUID | Primary key |
| name | VARCHAR(200) | Form name |
| slug | VARCHAR(63) | Unique slug per org |
| description | TEXT | Optional description |
| entity_definition_id | UUID | FK to entity_definitions (entity to populate) |
| config | JSONB | Form field config and behavior |
| is_active | BOOLEAN | Enable/disable |
| org_id | UUID | FK to orgs |
| created_at | TIMESTAMPTZ | Creation timestamp |
| updated_at | TIMESTAMPTZ | Last update timestamp |

**Unique constraint:** (org_id, slug)

#### form_links
Single-use, token-linked form invitations.

| Column | Type | Description |
|--------|------|-------------|
| id | UUID | Primary key |
| form_id | UUID | FK to forms |
| target_record_id | UUID | Optional: which record to update |
| token_hash | VARCHAR(64) | SHA-256 hash of the raw token (unique; used for public lookup) |
| status | VARCHAR(12) | pending, submitted, expired, revoked |
| recipient_email | VARCHAR(320) | Email to send invitation to |
| expires_at | TIMESTAMPTZ | When the link expires |
| submitted_at | TIMESTAMPTZ | When the form was submitted |
| created_by_id | UUID | FK to user_profiles (who generated the link) |
| org_id | UUID | FK to orgs |
| created_at | TIMESTAMPTZ | Creation timestamp |
| updated_at | TIMESTAMPTZ | Last update timestamp |

**Unique index:** `token_hash` (global, for public token resolution before RLS)

### Document Permissions (Per-Document Overrides)

The `documents` table has four additional columns (migration 015) for per-document permission overrides:

| Column | Type | Description |
|--------|------|-------------|
| view_permission_masks | BIGINT[] | Viewer access masks (empty = inherit from folder) |
| contributor_permission_masks | BIGINT[] | Contributor access masks (empty = inherit from folder) |
| viewer_permissions_config | JSONB | Viewer config (null = inherit from folder) |
| contributor_permissions_config | JSONB | Contributor config (null = inherit from folder) |

**Precedence:** If a per-document config is set (not null), it overrides the folder's masks for this document.
If null, the document's access falls back to its folder's masks.

## Row-Level Security

### RLS-Enabled Tables

The following tables have RLS policies enforced:
- **Permission dimensions:** regions, departments, roles, groups
- **Documents & organization:** folders, tags, documents, chat_sessions, user_org_memberships
- **Custom entities:** entity_definitions, entity_fields, entity_relationships, ce_* (all dynamic tables)
- **Workflows:** workflows, workflow_versions, workflow_outbox, workflow_runs, workflow_run_steps
- **Forms:** forms, form_links

### Policy Structure

Each table has four policies:

```sql
-- SELECT: Can only read rows matching tenant
CREATE POLICY tenant_isolation_select ON {table}
FOR SELECT
USING (org_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid);

-- INSERT: Can only insert rows matching tenant
CREATE POLICY tenant_isolation_insert ON {table}
FOR INSERT
WITH CHECK (org_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid);

-- UPDATE: Can only update rows matching tenant
CREATE POLICY tenant_isolation_update ON {table}
FOR UPDATE
USING (org_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid);

-- DELETE: Can only delete rows matching tenant
CREATE POLICY tenant_isolation_delete ON {table}
FOR DELETE
USING (org_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid);
```

> **RED-3 hardening (migration 002):** the tenant GUC is normalised with
> `nullif(current_setting(...), '')` before the `::uuid` cast. On a pooled
> connection a set-then-reverted GUC reads back as the empty string `''` (not
> NULL); a bare `''::uuid` cast raises `invalid input syntax for type uuid`,
> turning a benign "no tenant context" state into an error on the next RLS query.
> `nullif('', '')` normalises to NULL, so an unset/empty tenant deterministically
> returns zero rows and blocks all writes — **fail closed and error-free**. The
> Go `api-go` schema carries the identical hardening in migration `003`.
>
> The normalisation is **empty-string-specific by design**: a *non-empty*
> malformed tenant value (e.g. `'not-a-uuid'`) still raises on the `::uuid` cast
> (fail-loud). That is acceptable — the tenant id is always a validated `uuid.UUID`
> sourced from the authenticated request context, never from connection pooling —
> and a loud failure on a genuinely corrupt value is preferable to silently
> scoping to no tenant.

### Tenant Context

The API sets the tenant context per-request:

```python
await session.execute(
    text("SET LOCAL app.current_tenant_id = :tenant_id"),
    {"tenant_id": str(org_id)}
)
```

### Database Roles

| Role | Permissions | Purpose |
|------|-------------|---------|
| app_user | SELECT, INSERT, UPDATE, DELETE (RLS applies) | Tenant-scoped requests drop to this role via `SET LOCAL ROLE app_user` in `get_tenant_db` so RLS is enforced |
| app_admin | BYPASSRLS | Migrations, admin operations |

> **Adding a new tenant table:** grant `app_user` DML on it in the same
> migration (`GRANT SELECT, INSERT, UPDATE, DELETE ON <table> TO app_user`), or
> tenant requests will fail once they `SET ROLE app_user`. Migration
> `007_ensure_app_user_role` grants `ALL TABLES IN SCHEMA public` at its point in
> history, but that does not cover tables created by later migrations.

## Migrations

Migrations are managed with Alembic:

```bash
# Create a new migration
cd services/api
alembic revision --autogenerate -m "description"

# Apply migrations
alembic upgrade head

# Rollback
alembic downgrade -1
```

### Migration Files

Located in `services/api/alembic/versions/`:

**Core schema:**
- `001_initial_schema_with_rls.py` — Core tables and RLS policies
- `002_harden_rls_nullif.py` — RED-3: fail-closed RLS on empty tenant GUC (`nullif(..., '')`)
- `003_keycloak_to_clerk.py` — Rename `keycloak_sub` → `auth_subject`
- `004_user_deactivation.py` — Add `user_profiles.is_active` column
- `007_ensure_app_user_role.py` — Create `app_user` (NOSUPERUSER/NOBYPASSRLS) + grants
- `010_enable_pg_trgm.py` — Enable pg_trgm extension for text search

**Custom entities:**
- `008_custom_entities_catalog.py` — Create `entity_definitions`, `entity_fields`, `entity_relationships` tables

**Workflows:**
- `009_workflow_engine.py` — Create `workflows`, `workflow_versions` (static) and `workflow_outbox`, `workflow_runs`, `workflow_run_steps` (RANGE-partitioned by created_at); immutability trigger on published versions; `workflow_ensure_partitions(months_ahead)` PL/pgSQL function
- `012_workflow_run_permission.py` — Add `workflows.run_permission` JSONB column
- `013_workflow_outbox_source.py` — Add `workflow_outbox.source` column (trigger source)
- `014_workflow_run_delay.py` — Add delayed/scheduled workflow run support

**Forms:**
- `011_intake_forms.py` — Create `forms`, `form_links` tables (token_hash indexed globally for public resolution)

**Document permissions:**
- `015_add_document_permissions.py` — Add per-document permission mask + config columns to `documents` (override folder)

**Security:**
- `016_encrypt_org_openai_key.py` — Encrypt existing `orgs.openai_api_key` rows at rest; introduces `ORG_ENCRYPTION_KEY` requirement

**Partition maintenance:**
- `workflow_ensure_partitions(months_ahead)` is called by Celery beat; manually invoke via `POST /api/internal/workflows/maintain-partitions`

## Indexes

Key indexes for query performance:

| Table | Index | Columns | Notes |
|-------|-------|---------|-------|
| documents | ix_documents_org_id | org_id | |
| documents | ix_documents_folder_id | folder_id | |
| documents | ix_documents_uploaded_by_id | uploaded_by_id | |
| folders | ix_folders_org_id | org_id | |
| folders | ix_folders_dot_path | dot_path | |
| user_profiles | ix_user_profiles_auth_subject | auth_subject | Clerk subject ID |
| document_tags | ix_document_tags_tag_id | tag_id | |
| entity_definitions | uq_entity_def_slug | (org_id, slug) | Unique per org |
| entity_fields | fk_entity_fields_definition | entity_definition_id | |
| workflows | uq_workflow_name | (org_id, name) | Unique per org |
| workflow_outbox | ix_wf_outbox_pending | seq (WHERE status = 'pending') | Claim ordering |
| workflow_outbox | ix_wf_outbox_entity | (org_id, entity_definition_id) WHERE status = 'pending' | Trigger lookup |
| workflow_runs | ix_wf_runs_workflow | (org_id, workflow_id, created_at) | Run history |
| workflow_run_steps | ix_wf_steps_run | (org_id, run_id, step_index) | Step ordering |
| forms | uq_form_slug | (org_id, slug) | Unique per org |
| form_links | ix_form_links_token_hash | token_hash (unique, global) | Public resolution |

## Cascade Behavior

### ON DELETE CASCADE

Deleting an org cascades to all tenant-scoped data:
- orgs → regions, departments, roles, groups
- orgs → folders → documents (folder_id SET NULL)
- orgs → tags, chat_sessions
- orgs → entity_definitions → entity_fields, entity_relationships, ce_* tables (DROP TABLE)
- orgs → workflows → workflow_versions, workflow_outbox, workflow_runs, workflow_run_steps
- orgs → forms → form_links
- user_profiles → user_org_memberships

**Note:** Deleting an `entity_definition` cascades to its `ce_<slug>` physical table (DROP TABLE IF EXISTS).

### Workflow Partition Cleanup

The RANGE-partitioned `workflow_outbox`, `workflow_runs`, `workflow_run_steps` tables retain partitions indefinitely (no TTL).
For long-term retention management, manually drop old partitions:

```sql
-- Drop all partitions before 2023-01-01
DROP TABLE IF EXISTS workflow_outbox_202212, workflow_outbox_202211, ...;
```

Or write a routine maintenance procedure (e.g., Celery beat task calling `DROP TABLE IF EXISTS workflow_outbox_<YYYYMM> CASCADE`)

### Brain API Cascade

Org deletion also triggers best-effort cleanup in:
- Qdrant (both {tenant_id}_chunks and {tenant_id}_documents collections)
- Neo4j (all nodes with matching tenant label)

This is handled via API -> brain-api HTTP call, logged but not blocking on failure.

## Backup & Recovery

```bash
# Full backup
pg_dump -h localhost -p 5433 -U redarch -d redarch_km > backup.sql

# Restore
psql -h localhost -p 5433 -U redarch -d redarch_km < backup.sql
```

For production, use point-in-time recovery with WAL archiving.
