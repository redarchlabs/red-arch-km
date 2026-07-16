# RBAC (Role-Based Access Control)

How KM2 decides *who may do what* to *which* data. This doc covers the platform's
authorization layers — administrative roles, the 32-bit access-mask model for folders
and documents, per-workflow run permissions, per-entity/per-field access control, and how
API-key scopes fit in. It is for engineers and evaluators. Identity and *authentication*
(Clerk JWT verification, API-key hashing, RLS session setup) are owned by
[AUTHENTICATION.md](AUTHENTICATION.md); this doc references it rather than repeating it.

## Table of Contents

- [Layers at a glance](#layers-at-a-glance)
- [Administrative roles](#administrative-roles)
- [Tenant isolation (RLS) as the base layer](#tenant-isolation-rls-as-the-base-layer)
- [The 32-bit access mask](#the-32-bit-access-mask)
- [Folder & document permissions](#folder--document-permissions)
- [Workflow run permissions](#workflow-run-permissions)
- [Entity & field access control](#entity--field-access-control)
- [API keys & scopes](#api-keys--scopes)
- [Migration reference](#migration-reference)
- [Security considerations](#security-considerations)
- [Known gaps / TODO](#known-gaps--todo)

## Layers at a glance

Authorization is evaluated in independent layers; a request must pass all that apply:

| Layer | Question it answers | Enforced by |
|-------|---------------------|-------------|
| Tenant isolation | Is this row in my org? | Postgres RLS (every FORCE-RLS table) |
| Administrative role | Am I a site admin / org admin / member? | `is_site_admin`, `is_org_admin` on the auth context |
| Access mask | Can I see this folder/document/chunk? | `access_mask` package + resolved masks |
| Workflow run permission | May I manually run this workflow? | `workflows.run_permission` + `can_run()` |
| Entity/field access | May I write this entity / read this field? | `write_access`, `read_access` + `privileged` repo flag |
| API-key scope | Does this key grant this `/api/v1` operation? | `api_keys.scopes` + `require_scope()` |

## Administrative roles

There are three role tiers. They are booleans on two different tables, resolved into the
request's auth context in `services/api/src/api/auth/dependencies.py`.

| Role | Flag / source | Scope | Set by |
|------|---------------|-------|--------|
| Site admin | `user_profiles.is_site_admin` | Platform-wide (all orgs) | First-run bootstrap, then existing site admins |
| Org admin | `user_org_memberships.is_org_admin` | One org | Org admins or site admins |
| Member | A membership row with `is_org_admin = false` | One org, filtered by masks | Org admins |

### How the context is built

- `get_current_user` verifies the bearer token, provisions/loads the `UserProfile`, and
  returns a frozen `CurrentUser(sub, username, email, profile_id, is_site_admin)`. A
  deactivated profile (`is_active = false`) is refused with **403** even when the Clerk JWT
  is valid (`_ensure_active`).
- `require_org_access` loads the caller's `UserOrgMembership` for the requested org
  (eager-loading its regions/departments/roles/groups) and returns an
  `OrgContext(user, org_id, membership, is_org_admin)`.
  - A **site admin with no membership** in the org still gets access: a synthetic
    membership is created and `is_org_admin` is forced `True`. So
    `is_org_admin == membership.is_org_admin OR user.is_site_admin`.
  - A non-site-admin with no membership is refused with **403**.
- `require_org_admin` gates org-management endpoints (403 unless `ctx.is_org_admin`).
- `require_site_admin` gates platform endpoints (403 unless `user.is_site_admin`).

### Site admin vs org admin

Site admins are the platform superusers: they can CRUD organizations, manage users, and
implicitly act as an admin inside any org. The Site Admin console, its tabs, the first-run
bootstrap, and the guardrails (e.g. the last active site admin cannot be demoted, admins
cannot demote themselves) are documented in [SITE_ADMIN.md](SITE_ADMIN.md). Org admins have
full authority *within their own org only* — they bypass the access-mask, workflow-run, and
entity/field policies described below, but never cross the tenant boundary.

## Tenant isolation (RLS) as the base layer

Before any RBAC check runs, Postgres Row-Level Security guarantees a request can only touch
rows in its own org. Every tenant table is `FORCE ROW LEVEL SECURITY`; the app connects as
the non-superuser `km_app` role (which cannot bypass RLS). Two cross-cutting mechanisms
govern visibility, both set per-transaction in `services/api/src/api/db_scope.py`:

- **Per-tenant path** (`enter_tenant`): drops to role `app_user`, sets
  `app.current_tenant_id`, and forces `app.bypass = 'off'`. The tenant policy
  (`org_id = current_setting('app.current_tenant_id')::uuid`) filters every row.
- **Bypass path** (`enter_bypass`): sets `app.bypass = 'on'`. Migration
  `034_admin_bypass_policies` adds a permissive `admin_bypass_all` policy
  (`USING`/`WITH CHECK (current_setting('app.bypass', true) = 'on')`) to every FORCE-RLS
  table. RLS policies are OR-combined, so a row is visible/writable when *either* the tenant
  policy matches *or* the bypass GUC is on. Only the enumerated privileged paths
  (`get_db`, workflow/agent poll sweeps, site-admin, provisioning, API-key resolution) turn
  it on; normal request paths never do.

This replaced the older Postgres-superuser bypass so the stack runs on managed Postgres
(e.g. Cloud SQL) where the superuser cannot hold `BYPASSRLS`. The full model lives in
[DATABASE.md](DATABASE.md) and [AUTHENTICATION.md](AUTHENTICATION.md#3-authorization--postgres-rls);
**RLS is tenant isolation, not RBAC** — the layers below run on top of it.

## The 32-bit access mask

Folder and document visibility is expressed as a 32-bit integer. The `access_mask` package
(`packages/access_mask/src/access_mask/`) encodes/decodes/matches these masks; the same
layout is mirrored in Go at `packages/accessmask/mask.go`.

### Dimensions and bit layout

Five dimensions pack into 32 bits (`packages/access_mask/src/access_mask/constants.py`):

| Dimension | Bits | Shift | Max value |
|-----------|------|-------|-----------|
| Org | 11 | 21 | 2047 (`MAX_ORG_ID`) |
| Region | 5 | 16 | 31 (`MAX_REGION`) |
| Role | 5 | 11 | 31 (`MAX_ROLE`) |
| Group | 7 | 4 | 127 (`MAX_GROUP`) |
| Dept | 4 | 0 | 15 (`MAX_DEPT`) |

**Total: 11 + 5 + 5 + 7 + 4 = 32 bits.**

```
 31                                                       0
 ├─── Org (11) ───┼─ Region (5) ─┼─ Role (5) ─┼─ Group (7) ─┼─ Dept (4) ─┤
     bits 21-31       16-20         11-15         4-10         0-3
```

### Encoding, decoding, matching

```python
from access_mask import encode, decode, matches

# Encode a user's permissions (keyword-only; each value is range-validated)
user_mask = encode(org=1, region=3, role=2, group=7, dept=5)

decode(user_mask)
# DecodedMask(org=1, region=3, role=2, group=7, dept=5)

# A document open to ANY role/group in Engineering (dept 5), North America (region 3)
doc_mask = encode(org=1, region=3, dept=5, role=31, group=127)
matches(user_mask, doc_mask)  # True
```

`encode` raises `ValueError` if any component is out of range. `matches(user_mask, doc_mask)`
implements the access rule (`access_mask.mask`):

- **Org must match exactly** — there is no org wildcard, so a mask can never grant
  cross-org access even before RLS.
- Every other dimension matches when the **document** value is the wildcard (its `MAX`)
  **or** equals the user's value (`_field_matches`).

### Wildcards

Setting a dimension to its `MAX` on the *document/folder* side means "any user value":

| Dimension | Wildcard |
|-----------|----------|
| Region | 31 |
| Role | 31 |
| Group | 127 |
| Dept | 15 |
| Org | (none — must match exactly) |

```python
# Folder open to ALL roles/groups in Engineering, North America
encode(org=1, region=3, dept=5, role=31, group=127)
```

### Multiple masks

A folder or document carries a **list** of masks; a user is granted access if their mask
matches **any** entry (logical OR):

```python
# Accessible to Engineering OR Sales, any region/role/group
view_permission_masks = [
    encode(org=1, dept=5, region=31, role=31, group=127),  # Engineering
    encode(org=1, dept=2, region=31, role=31, group=127),  # Sales
]
```

A user asserts **all** the masks their membership implies — the Cartesian product of their
assigned regions × departments × roles × groups
(`calculate_user_masks_from_membership` in
`services/api/src/api/services/permission_config.py`). Access is granted if any user mask
matches any resource mask.

## Folder & document permissions

Folders and documents are gated by two mask lists each: `view_permission_masks` and
`contributor_permission_masks`. Documents were given their own columns in migration
`015_add_document_permissions` (mirroring the four columns already on `folders`).

### Permission config → masks

Admins don't hand-write integers. Each resource stores a human-readable
`viewer_permissions_config` / `contributor_permissions_config` — a **list of entries**,
each entry a dict of *singular* dimension names to a value
(`list[dict[str, Any]]`, see `services/api/src/api/schemas/document.py`):

```json
[
  { "region": "North America", "department": "Engineering" },
  { "department": "Sales" }
]
```

Semantics (`permission_config_to_masks` in `permission_config.py`):

- **Within one entry**, all named dimensions must match (logical AND). A dimension omitted
  from the entry becomes its wildcard `MAX`.
- **Across entries**, the resulting masks are OR'd (the example above = "Engineering in
  North America" OR "Sales anywhere").
- Names are resolved to each dimension's `permission_number` from the org's
  Region/Department/Role/Group tables; an unresolvable name is logged and skipped.

`compute_folder_masks` (`services/api/src/api/services/folder_service.py`) wraps this for
both viewer and contributor configs; the folder/document routers persist the resulting
integer lists whenever the config is created or changed.

### Inheritance (documents ← folder)

A document's own `viewer_permissions_config` may be **NULL**, meaning "no per-document
override — inherit the folder." A non-NULL config overrides the folder for that document.
This lets a single document be tightened or loosened without moving it or nesting folders.
Existing rows were left NULL so they keep inheriting (matching pre-migration behaviour).

### Feeding the knowledge brain (search / RAG chat)

A document's resolved masks become the `access_keys` stored with each of its chunks in the
brain (Qdrant payload) at ingest time. At query time the API translates the *requester*
into the same mask space and passes it to the brain so only matching chunks are returned
(`services/api/src/api/services/search_access.py`):

- **A user (Clerk session)** → `resolve_user_access_keys` returns the membership masks —
  **or `None` for org admins**, meaning unrestricted.
- **An org API key** → `service_key_access_keys` returns `None` (org-wide). An org key is an
  org-level credential: its *operations* are gated by scopes, but its *data visibility* is
  org-wide by design.

Chats/searches can additionally be scoped to specific folders via synthetic `folder:<id>`
tags (`folder_tags`).

## Workflow run permissions

Manually running a workflow (`POST /api/workflows/{workflow_id}/run`) is gated per workflow
by the `workflows.run_permission` JSONB column (migration `012_workflow_run_permission`,
default `{"mode": "org_admin"}`). The schema is `RunPermission`
(`services/api/src/api/schemas/workflow.py`) with `extra="forbid"`:

```json
{ "mode": "roles", "role_ids": ["uuid"], "group_ids": ["uuid"] }
```

| Mode | Who may run |
|------|-------------|
| `org_admin` | Org admins only (default) |
| `any_member` | Any member of the org |
| `roles` | Members whose membership includes any listed `role_ids` **or** `group_ids` |

Enforcement is `can_run(ctx, run_permission)` in
`services/api/src/api/services/workflow/permissions.py`:

- **Org admins always pass**, regardless of mode.
- `any_member` → any org member passes.
- `roles` → the caller's membership roles/groups must intersect the configured
  `role_ids`/`group_ids`. (There is a single `roles` mode that covers *both* roles and
  groups; there is no separate `specific_groups` mode.)
- Unknown/`org_admin` mode → only admins.

The same `can_run` check gates the chat agent's `run_workflow` tool — it honors the
workflow's own `run_permission`, it is **not** silently admin-gated (`services/api/src/api/services/agent.py`).
Automation triggered by record changes (create/update/delete) fires regardless of which user
made the change; run permissions only govern *manual* runs. See
[WORKFLOW_ENGINE.md](WORKFLOW_ENGINE.md).

## Entity & field access control

By default any org member may CRUD records of a custom entity through the record API. Two
opt-in policies (migration `039_entity_access_control`) let an org lock down a record
surface so members can read/interact with it but cannot tamper with it — e.g. a quiz answer
key or a certification. Both default to the pre-existing fully-open behaviour, so existing
entities are unaffected until an admin opts in.

| Policy | Column | Values (default first) | Effect |
|--------|--------|------------------------|--------|
| Entity write | `entity_definitions.write_access` | `member`, `workflow_only` | `workflow_only` → direct member writes (create/update/delete) are refused |
| Field read | `entity_fields.read_access` | `member`, `server_only` | `server_only` → the field's values are hidden from members and cannot be filtered/sorted/grouped on |

### The `privileged` flag

Enforcement lives in `DynamicEntityRepository`
(`services/api/src/api/repositories/dynamic_entity.py`). The repository takes a
`privileged: bool` flag (default `False`). A **privileged** repo bypasses both policies; a
**non-privileged** repo is subject to them. Who is privileged:

| Caller | Built via | Privileged? |
|--------|-----------|-------------|
| Workflow engine (runner/dispatcher) | `build_record_repo(..., privileged=True)` | **Yes** |
| Course generation (server-side) | `build_record_repo(..., privileged=True)` | **Yes** |
| Record API — org admin | `build_record_repo(..., privileged=ctx.is_org_admin)` | **Yes** (admin) |
| Record API — member | same, `is_org_admin` false | No |
| `/api/v1` API-key records | `build_record_repo(session, principal.org_id, slug)` | **No** (default) |
| Chat agent record tools | `build_record_repo(ctx.session, ...)` | **No** (default) |

So the workflow engine and org admins bypass the policy; regular members, API keys, and the
chat agent do not. `build_record_repo` (`services/api/src/api/services/entity_records_helpers.py`)
threads the flag through.

### How the two policies are enforced

- **`write_access = workflow_only`**: on any create/update/delete, `_guard_writable` raises
  `RecordAccessError` for a non-privileged caller. It is deliberately *not* an
  `EntityRecordError` (400) subclass — a dedicated handler (`make_record_access_handler` in
  `services/api/src/api/exception_handlers.py`) maps it to a clean **403**.
- **`read_access = server_only`**: the repo computes `_server_only_slugs` at construction.
  For a non-privileged caller it:
  - strips those keys from any write payload before validation (`_to_row`) — a member
    update to the rest of the record still succeeds; the protected field is silently
    ignored, never written;
  - omits those columns from every read projection;
  - **refuses to filter/sort/group by them** — otherwise their values would leak via a
    filter/substring oracle.

### Why this makes records tamper-proof

The LMS uses this to make its gates real rather than theatre. `question.correct_answer` and
`scenario.rubric` are `server_only`, so a learner cannot read the answer key or grading
rubric through the record API. `assessment_attempt`, `simulation_attempt`, and
`certification` are `workflow_only`, so a learner cannot forge a passing attempt or mint
their own certificate — only the grading workflow (which runs privileged) can write them.
Course generation writes privileged precisely so the `server_only` answer key survives the
write (a non-privileged write would drop it). Full walkthrough:
[LMS.md](LMS.md#tamper-proofing-via-entity-access-control).

## API keys & scopes

The versioned public surface (`/api/v1/**`) is authenticated by an **organization API key**
(`km2_…`, SHA-256 hashed, migration `028_api_keys`), not a Clerk session. Creation, storage,
hashing, and the auth path are documented in
[AUTHENTICATION.md](AUTHENTICATION.md#5-api-key-authentication-apiv1); this section covers
only the *authorization* dimension — scopes.

Each key carries a JSONB `scopes` list. An endpoint declares the one concrete scope it needs
via `require_scope("<domain>:<action>")` (`services/api/src/api/auth/api_key.py`), and
`has_scope` decides access (missing scope → **403**). The canonical catalog is
`API_SCOPES` in `services/api/src/api/services/api_key_scopes.py`:

| Scope | Grants |
|-------|--------|
| `entities:read` | Read custom entity definitions (schema) |
| `records:read` / `records:write` | Read / create-update-delete entity records |
| `reports:read` / `reports:run` | Read saved reports / execute reports & aggregations |
| `workflows:read` / `workflows:run` | Inspect workflows & runs / trigger runs (high privilege) |
| `search:read` | Semantic search & RAG chat |
| `knowledge:read` | Read documents & folders |
| `agents:read` / `agents:run` | Inspect / run agents |
| `work_orders:read` / `work_orders:write` | Read / file & update work orders |
| `config:read` / `config:write` | Read instance config / receive & apply config promotions |

Rules that matter for authorization:

- **Wildcards** (`*` and `<domain>:*`) may be *granted* to a key, but an endpoint always
  requires a concrete scope (`normalize_scopes`).
- **`config:write` is a sensitive scope** (`SENSITIVE_SCOPES`): it can rewrite the whole org
  configuration (used by change-management promotions), so it is **never** covered by a `*`
  or `config:*` wildcard — a key must list `config:write` explicitly.
- An API key's *data* visibility is org-wide (see [search](#feeding-the-knowledge-brain-search--rag-chat));
  scopes only constrain which *operations* it can perform. Notably, `/api/v1` record access
  is never privileged, so a key cannot write `workflow_only` entities or read `server_only`
  fields even with `records:write`.

## Migration reference

| Migration | Adds |
|-----------|------|
| `012_workflow_run_permission` | `workflows.run_permission` JSONB |
| `015_add_document_permissions` | Per-document viewer/contributor config + mask columns |
| `028_api_keys` | `api_keys` table (hashed key, scopes, RLS) |
| `034_admin_bypass_policies` | GUC-gated `admin_bypass_all` RLS policy on every FORCE-RLS table |
| `039_entity_access_control` | `entity_definitions.write_access`, `entity_fields.read_access` |

## Security considerations

1. **RLS is the floor.** Cross-tenant access is impossible without the bypass GUC, which
   only enumerated privileged paths set. The org dimension of the access mask also has no
   wildcard, giving a second cross-org guard.
2. **Fail-closed enforcement.** `workflow_only` writes 403 via a dedicated handler (not a
   swallowed 400); `server_only` fields are dropped from writes and excluded from
   filter/sort/group so they cannot leak via an oracle.
3. **Range-validated masks.** `encode` rejects out-of-range dimension values before packing.
4. **Least privilege by default.** New entities/fields are fully open; write/read lockdown
   is opt-in. API keys must be minted with explicit scopes; `config:write` is never granted
   by a wildcard.
5. **Deactivation is immediate.** A deactivated profile is refused at auth time (403) even
   with a valid JWT.
6. **Secret handling and auth mechanics** (JWT verification, key hashing, per-org OpenAI key
   encryption, internal/brain service keys, webhook signing) are owned by
   [AUTHENTICATION.md](AUTHENTICATION.md#11-security-notes).

## Known gaps / TODO

- **Contributor masks vs write path.** `contributor_permission_masks` are computed and
  stored for folders/documents, but this doc does not trace every endpoint that consults
  them for write authorization; verify against the folder/document routers before relying on
  contributor-level write gating for a new surface.
- **No per-record ACLs.** Access is by folder/document mask, per-entity write policy, and
  per-field read policy — there is no per-*record* owner/ACL model. Record-scoping patterns
  (e.g. the LMS `@me` filter) are conventions built in views/workflows, not an enforced
  RBAC primitive.

> Reviewed 2026-07-16 against Alembic migration 039. Source of truth is the code; if this doc disagrees with the code, the code wins.
