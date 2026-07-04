# Database

Red Arch KM uses PostgreSQL 18 with Row-Level Security (RLS) for multi-tenant data isolation.

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
User identities synchronized from Keycloak.

| Column | Type | Description |
|--------|------|-------------|
| id | UUID | Primary key |
| keycloak_sub | VARCHAR(255) | Keycloak subject ID (unique) |
| username | VARCHAR(150) | Unique username |
| email | VARCHAR(254) | Unique email |
| description | TEXT | Profile description |
| is_site_admin | BOOLEAN | Platform-wide admin flag |
| created_at | TIMESTAMPTZ | Creation timestamp |
| updated_at | TIMESTAMPTZ | Last update timestamp |

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

## Row-Level Security

### RLS-Enabled Tables

The following tables have RLS policies enforced:
- regions, departments, roles, groups
- folders, tags, documents, document_access
- document_attribute_definitions, chat_sessions
- user_org_memberships

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
| app_user | SELECT, INSERT, UPDATE, DELETE (RLS applies) | Application service account |
| app_admin | BYPASSRLS | Migrations, admin operations |

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
- `001_initial_schema_with_rls.py` — Core tables and RLS policies
- `002_harden_rls_nullif.py` — RED-3: fail-closed RLS on empty tenant GUC (`nullif(..., '')`)

## Indexes

Key indexes for query performance:

| Table | Index | Columns |
|-------|-------|---------|
| documents | ix_documents_org_id | org_id |
| documents | ix_documents_folder_id | folder_id |
| documents | ix_documents_uploaded_by_id | uploaded_by_id |
| folders | ix_folders_org_id | org_id |
| folders | ix_folders_dot_path | dot_path |
| user_profiles | ix_user_profiles_keycloak_sub | keycloak_sub |
| document_tags | ix_document_tags_tag_id | tag_id |
| document_access | ix_document_access_folder_id | folder_id |

## Cascade Behavior

### ON DELETE CASCADE

Deleting an org cascades to all tenant-scoped data:
- orgs -> regions, departments, roles, groups
- orgs -> folders -> documents (folder_id SET NULL)
- orgs -> tags, chat_sessions, document_attribute_definitions
- user_profiles -> user_org_memberships

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
