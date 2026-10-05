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
assigned regions × departments × roles × groups (`member_base_masks` in
`services/api/src/api/services/permission_config.py`), each **expanded with its wildcard
variants** (`calculate_user_masks_from_membership` → `access_mask.expand_member_masks`).
Access is granted if any user mask equals any resource mask.

### Wildcards are matched by expansion

Folder and document masks use a dimension's `MAX` as "any value", but none of the places
that filter on masks can evaluate `matches()`: folder visibility uses Postgres array
overlap, Qdrant uses `MatchAny`, and the fact graph uses `k IN $keys` — all integer
equality. So each member mask is expanded with every combination of *own value* /
*wildcard* across region, role, group and dept (up to 16 variants; the org is never
wildcarded). `doc_mask ∈ expand_wildcards(user_mask)` is exactly `matches(user_mask,
doc_mask)` — this is unit-tested exhaustively over a grid in both `access_mask` (Python)
and `accessmask` (Go, `ExpandWildcards` / `ExpandMemberMasks`).

The expansion lives in **one** place, `calculate_masks_from_assignments` (which
`calculate_user_masks_from_membership` delegates to), and every resolver uses it: members
(`resolve_user_access_keys`, the document and folder lists), agent actors
(`resolve_profile_access_keys`) and dimension-scoped API keys (`resolve_key_scope`). Nothing changes on the
document side: stored masks are unchanged and nothing needs re-ingesting.

> **Behaviour change (migration-free, effective on deploy).** Before this, a folder whose
> viewer config left any dimension unnamed — e.g. `{"department": "Finance"}`, or
> `{"region": "West", "department": "Finance"}` — matched **no member at all** (only org
> admins, who bypass masks, could see it), in folder lists, document lists, search and the
> fact graph. Such folders now become visible to the members they name, as the config
> always said they should. Admins should review folder configs that were written
> expecting them to behave as "admins only": that was never the documented meaning, but it
> was the effective one. Folders that name every dimension, and public folders, are
> unaffected.

**Size and cap.** The set is built per dimension (`access_mask.member_masks`, mirrored by
Go `MemberMasks`): each dimension contributes its assigned values plus its wildcard, so a
member sends `(regions+1) × (departments+1) × (roles+1) × (groups+1)` masks (an unassigned
dimension counts as one value, `0`), plus the public sentinel. Examples: one of each →
16; 3 regions × 2 departments × 2 roles × 1 group → 4·3·3·2 = 72; 9 of everything →
10 000. The cap is `MAX_ACCESS_KEYS = 8192`, shared by the API and every brain-api request
that takes `access_keys` (search, chat, ask, agent ask, gap re-extract). A membership over
it fails closed with a `422` that says why (`TooManyAccessMasks`; an agent's knowledge
tool returns an error instead), never a truncated — and therefore different — filter.
Measured on Neo4j 5.25 with 5 000 claims, the claim filter's cost barely moves with list
size (`query_claims` ≈ 5 ms with 2 keys, ≈ 10 ms with 8 192; graph context ≈ 25 → 36 ms),
so the `k IN $keys` form is kept.

**No dimension at its wildcard number.** A region numbered 31 (role 31, group 127,
department 15) would itself read as "any" on the folder side. Creating a dimension
therefore stops at `MAX-1` with a `409` (`DimensionLimitReached` in
`repositories/dimension.py`). Existing rows are not touched; to find any already at the
wildcard value (read-only):

```sql
SELECT 'region' AS dim, org_id, name, permission_number FROM regions WHERE permission_number >= 31
UNION ALL SELECT 'role', org_id, name, permission_number FROM roles WHERE permission_number >= 31
UNION ALL SELECT 'group', org_id, name, permission_number FROM groups WHERE permission_number >= 127
UNION ALL SELECT 'department', org_id, name, permission_number FROM departments WHERE permission_number >= 15
ORDER BY org_id, dim;
```

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
- **An agent run** → `resolve_profile_access_keys` gives the run's *actor* the same masks
  (`None` for an admin actor; `[]` — no access — for a profile with no membership).
- **An org API key** (`access_mode = 'org'`) → `service_key_access_keys` returns `None`
  (org-wide). An org key is an org-level credential: its *operations* are gated by
  scopes, but its *data visibility* is org-wide by design.
- **A scoped API key** → `api_key_access_keys` returns the masks of its own dimension
  assignments and `api_key_folder_scope` its folder set, resolved on every request (see
  [Dimension-scoped API keys](#dimension-scoped-api-keys)). Masks are never `None`, never
  empty.

**Empty-list contract.** For brain-api an absent or `null` `access_keys` / `folder_tags`
is *no filter* and an explicit `[]` is *nothing readable* (empty answer, nothing queried).
`BrainAPIClient` sends exactly what it is given, so unrestricted callers (org admins, org
keys, trusted workflows) pass `None` — never `[]`. Callers still refuse or short-circuit
an empty scope themselves (a folder-limited key whose allowed set is empty gets no results
without brain-api being called); the brain-api rule is the backstop that makes a missed
short-circuit fail closed instead of open.

Chats/searches can additionally be scoped to specific folders via synthetic `folder:<id>`
tags (`folder_tags`). **The `folder:` tag prefix is reserved**: it is folder membership in
the index, carried in the same list (Qdrant `tags`, Neo4j `d.tags`) as a document's own
tags, so a user tag spelled `folder:<id>` would forge membership of another folder. Tag
names starting with `folder:` (any case, after trimming) are refused (`422`) on create and
rename, skipped by config import, and dropped wherever the API builds a document's index
tags (`api.services.index_tags.index_tags`), so only the server-appended folder tag ever
carries the prefix. Tags created before this rule are not changed; list them (read-only)
with:

```sql
SELECT org_id, id, name FROM tags WHERE lower(btrim(name)) LIKE 'folder:%';
```

Rename or delete any it finds, then re-save (or reprocess) the documents that carry them so
their index tags are rebuilt without the forged entry.

### Facts follow every source

A fact is visible to whoever can see **at least one** document that states it. Each
claim's `access_keys` are therefore the union of its source documents' masks — empty
(public) if any source is public — and are recomputed when a source is added
(corroboration), removed (document deleted or reprocessed), moved or re-masked
(`update-document-metadata` → `Neo4jFactStore.update_document_access_keys`). A source's
masks are recorded on its `Document` node at ingest. So a fact first stated by a public
document and later also by an HR document becomes HR-only when the public document is
deleted.

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
| `knowledge:write` | Add documents / new versions by `external_ref` (`POST /api/v1/knowledge/documents`) |
| `agents:read` / `agents:run` | Inspect / run agents |
| `work_orders:read` / `work_orders:write` | Read / file & update work orders |
| `config:read` / `config:write` | Read instance config / receive & apply config promotions |

Rules that matter for authorization:

- **Wildcards** (`*` and `<domain>:*`) may be *granted* to a key, but an endpoint always
  requires a concrete scope (`normalize_scopes`).
- **`config:write` is a sensitive scope** (`SENSITIVE_SCOPES`): it can rewrite the whole org
  configuration (used by change-management promotions), so it is **never** covered by a `*`
  or `config:*` wildcard — a key must list `config:write` explicitly.
- An org API key's *data* visibility is org-wide (see [search](#feeding-the-knowledge-brain-search--rag-chat));
  scopes only constrain which *operations* it can perform. A scoped key narrows its
  *knowledge* visibility to its own dimension assignments and folders (next section). Notably, `/api/v1` record
  access is never privileged, so a key cannot write `workflow_only` entities or read
  `server_only` fields even with `records:write`.
- **`knowledge:write` is also sensitive**: each write starts an LLM-billed ingest and adds
  content every reader of the folder will see, so `*` / `knowledge:*` keys do not get it;
  it must be listed explicitly. Writes are additionally capped per key per day
  (`API_KEY_DOCUMENT_WRITES_PER_DAY`, default 500).

### Dimension-scoped API keys

A key may be minted with its own **dimension assignments** — any subset of regions, roles,
groups and departments (`api_key_regions`, `api_key_roles`, `api_key_groups`,
`api_key_departments`, migration `053`) — and/or a list of **folders** (`api_key_folders`).
Any assignment makes it `access_mode = 'scoped'`; none leaves it `'org'` (org-wide, the
behaviour of every existing key). No member profile backs a scoped key. Logic:
`services/api_key_scope.py` (resolution), `services/api_key_assignments.py` (mint-time
validation, labels, delete guard), `services/search_access.py`
(`api_key_access_keys` / `api_key_folder_scope`), `services/knowledge_visibility.py`.

**Masks.** `calculate_masks_from_assignments(org_number, regions=…, departments=…, roles=…,
groups=…)` builds the key's masks exactly as it builds a member's (an empty dimension is
`0`, i.e. unassigned; wildcard variants per dimension; plus the public sentinel). A key
holding only a role reads with exactly the masks of a member holding only that role. A key
with folders but no dimensions reads with the masks of a member with no assignments —
never org-wide.

**Folders only narrow.** The key's folder set is its listed folders plus all their
descendants, found by walking `parent_id` from the listed ids
(`FolderRepository.visible_subtrees`, a recursive CTE on the indexed `folders.parent_id`)
— never by `dot_path`: paths are built from names and two top-level folders may share a
name (the unique constraint does not cover `NULL` parents), so a path prefix would pull a
same-named root's whole subtree in. Only that subtree and its ancestors (for inherited
restrictions) are loaded, intersected with the folders its masks may see. A folder therefore never grants anything
the masks lack. Because the set is recomputed on every request, **moving a folder under a
listed folder widens the key's folder set** to include it (the masks still cap it), and a
listed folder whose viewer permissions are tightened beyond the masks drops out.

| Check | When | Result if it fails |
|-------|------|--------------------|
| Every id exists **in the key's org** (tenant session + explicit `org_id` filter — the join tables have no `org_id` and no RLS); each row is locked `FOR SHARE` until the mint commits | Minting (`POST /api/api-keys`) | `422`; missing and foreign ids share one message |
| Masks stay under `MAX_ACCESS_KEYS` | Minting | `422` |
| Every listed folder is visible to those masks | Minting | `422` naming the folders by path — the key could never read them |
| The listed folders plus their visible subfolders number at most 1024 (`MAX_FOLDER_TAGS`, what one search can carry) | Minting; again on every search | `422` |
| The deferred FKs hold (`SET CONSTRAINTS ALL IMMEDIATE` after the insert) | Minting, before the key is committed and its plaintext returned | `409` |
| Every scope is scope-aware and concrete (`validate_scoped_key_scopes`) | Minting | `400` — see below |
| Assignment rows load in one query; at least one row; every row resolves inside the key's org (`resolve_key_scope`) | **Every request**, in `require_api_key`; **every tool call** of a run the key started | `403` on every route (reason logged, not returned); `422` past the mask cap. Never falls back to org-wide |
| A held region/role/group/department/folder is deleted | `DELETE /api/dimensions/…`, `DELETE /api/folders/{id}` | The item is locked `FOR UPDATE` (waiting for any mint holding it), revoked/expired keys' rows are released (`purge_inactive_holders`, an explicit step — the check itself is read-only), then `409` naming the active keys. The FK is `ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED` — deferred because PostgreSQL checks a non-deferred FK at the end of each cascade query, which would break deleting the whole org — and the handler checks it immediately after the delete, so a violation is a `409` in the handler, never a failed commit after a `204` |
| Key deleted | — | its assignment rows cascade away |

Assignments are fixed at mint: there is no update route (revoke and re-issue).

**Scope-aware scopes only.** A scoped key may hold `search:read`, `knowledge:read`,
`knowledge:write`, `agents:read`, `agents:run`, `work_orders:read`, `work_orders:write`
(`SCOPED_KEY_SCOPES`) and no wildcard. Records, reports, entities, workflows and config
ignore the key's assignments, so a scoped key holding them would be org-wide there.
`require_scope` also refuses (`403`) any other scope for a scoped key, as defense in depth.

What the key's masks and folders govern:

- **Search / RAG chat**: the masks go to brain-api, which filters Qdrant chunks
  (`MatchAny` on `access_keys`) and fact-graph claims (`size(c.access_keys) = 0 OR any(k
  IN c.access_keys WHERE k IN $keys)`). For a folder-limited key, `folder_tags` for its
  folder set (or the requested subset) is always sent — for chat as well as search, and
  brain-api applies it to graph claims too (a claim is returned only if a source document
  is both inside the folders and visible to the masks). A requested folder outside the set
  is `404 folder not found`; an empty set short-circuits with no results; more than 1024
  folders is `422`.
- **Folders / documents / chunks / summary**: a folder is visible when its *effective*
  view masks are empty or overlap, and (folder-limited key) it is in the set; a document
  when it is filed in such a folder and its own viewer override (if set) admits the masks.
  Unfiled documents are not visible (they bypass folder permissions). brain-api's
  chunk/summary endpoints do not filter, so the API refuses (`404`) before calling them.
- **Adding documents** (`POST /api/v1/knowledge/documents`): the folder must be in the
  set (folder-limited key) and visible and, when it (or its nearest ancestor with one)
  sets a contributor config, the masks must overlap its contributor masks. This is the
  first write path that enforces contributor masks; first-party member uploads still do
  not (see Known gaps). The document has no uploader.
- **Replacing a document by `external_ref`**: additionally, the existing document must be
  in the set and visible (`409`, same text as any conflict, otherwise) and, when it has its
  own contributor config, the masks must overlap it (`403` otherwise).
- **Agent runs / work orders** started with the key have no actor (and no filing
  profile); they carry `api_key_id`. The key lists and reads only the runs and work orders
  it created, and lists agents as summaries only (no persona, params, grants or MCP
  servers).

**API-originated runs** (`agent_runs.via_api_key` / `api_key_id`, `work_orders.via_api_key`
/ `api_key_id`, migration `053`). Every run and work order created through `/api/v1` is
marked, and the mark is inherited by delegated, consulted, escalated and review runs and by
work-order continuations. In such a run the knowledge tool
(`services/agents/tools/knowledge.py`):

- refuses an `org` argument — a key is minted for one org;
- reloads the key first (`run_key_scope`, `services/agents/tools/key_scope.py`): a
  missing (deleted → `api_key_id` NULL), revoked, expired or unresolvable key refuses the
  call; a scoped key searches with the key's masks and folder set. This check comes
  **before** the unattended `knowledge_scope: "org"` branch, which would otherwise make an
  actor-less run unrestricted;
- for an org key's run behaves like any unattended run (needs `knowledge_scope: "org"`)
  — **including when a person started the key-filed work order** and the run therefore has
  an actor: the actor is ignored, so the run neither borrows an admin's reach nor is
  refused for having one.

**Every tool call** of a key-started run first re-checks the key with one primary-key read
(`run_key_status`: same org, not revoked, not expired, a known access mode); the full
mask/folder resolution runs only in the tools that use it. Once the key is gone, every
tool — allowlisted ones included — is refused with "The API key that started this run is
no longer valid…" and the run is finalized `error`; a run picked up with its key already
gone ends before its first turn.

**Tools in a run started by a scoped key** (`services/agents/tools/key_scope.py`). Most
agent tools are not mask-aware, so such a run — decided by reloading the key, never by
`actor_user_id`; a run whose key is gone counts as scoped (most restrictive) — is offered,
and at dispatch allowed, only `SCOPED_KEY_RUN_TOOLS`:

| Tool | Why it is safe |
|------|----------------|
| `search_knowledge` | the key's masks and folders, own org only, never unrestricted |
| `create_document` | needs `knowledge:write` on the key (re-checked at call time: still active, still holding it — `agent_runs.api_key_id`), counts against the key's daily write cap, a folder in the key's set that its masks may add to; no unfiled documents; no uploader |
| `set_work_order_tasks`, `update_work_order_task`, `list_work_order_tasks`, `submit_plan`, `complete_task`, `escalate_task` | the run's own work order |
| `delegate_task`, `escalate`, `consult_peer`, `reply_to_peer`, `ask_human`, `request_review` | spawned runs inherit `via_api_key` + `api_key_id`, so the same limits |
| `web_research`, `fetch_web_page` | the public web, no org data |

Everything else is refused: `list_records`, `get_record`, `create_record`,
`update_record`, `attach_document`, `list_work_order_documents`,
`read_work_order_document`, `list_workflows`, `run_workflow`, `read_run_detail`,
`batch_generate`, `check_batch`, `run_claude_code` and every MCP tool. Runs started by an
org key or by a person are not restricted by this list. For every run,
`create_document` metadata must be an object and may not set the reserved index fields.

An **org** key may add documents to any folder in its org — the same reach as an org
admin, gated only by the explicit `knowledge:write`, through the REST write and through
`create_document` in runs it started alike; both count against the key's
`API_KEY_DOCUMENT_WRITES_PER_DAY` cap.

## Migration reference

| Migration | Adds |
|-----------|------|
| `012_workflow_run_permission` | `workflows.run_permission` JSONB |
| `015_add_document_permissions` | Per-document viewer/contributor config + mask columns |
| `028_api_keys` | `api_keys` table (hashed key, scopes, RLS) |
| `034_admin_bypass_policies` | GUC-gated `admin_bypass_all` RLS policy on every FORCE-RLS table |
| `039_entity_access_control` | `entity_definitions.write_access`, `entity_fields.read_access` |
| `053_api_key_dimension_scope_and_document_external_ref` | `api_keys.access_mode` (`org`/`scoped`); `api_key_regions` / `_roles` / `_groups` / `_departments` / `_folders` (key FK CASCADE, item FK NO ACTION deferred); `documents.external_ref` (unique per folder) + `content_hash`; `agent_runs` / `work_orders` `via_api_key` + `api_key_id`; `ix_folders_parent_id` (a key's folders expand by `parent_id`). Developers who applied an earlier draft (with `api_keys.profile_id`) must `alembic downgrade 052` first |

## Security considerations

1. **RLS is the floor.** Cross-tenant access is impossible without the bypass GUC, which
   only enumerated privileged paths set. The org dimension of the access mask also has no
   wildcard, giving a second cross-org guard.
2. **Fail-closed enforcement.** `workflow_only` writes 403 via a dedicated handler (not a
   swallowed 400); `server_only` fields are dropped from writes and excluded from
   filter/sort/group so they cannot leak via an oracle.
3. **Range-validated masks.** `encode` rejects out-of-range dimension values before packing.
4. **Least privilege by default.** New entities/fields are fully open; write/read lockdown
   is opt-in. API keys must be minted with explicit scopes; `config:write` and
   `knowledge:write` are never granted by a wildcard.
5. **Deactivation is immediate.** A deactivated profile is refused at auth time (403) even
   with a valid JWT.
6. **Secret handling and auth mechanics** (JWT verification, key hashing, per-org OpenAI key
   encryption, internal/brain service keys, webhook signing) are owned by
   [AUTHENTICATION.md](AUTHENTICATION.md#11-security-notes).

## Known gaps / TODO

- **Contributor masks vs first-party writes.** `contributor_permission_masks` are enforced
  only for scoped API keys writing through `POST /api/v1/knowledge/documents`. The
  first-party `POST /api/documents`, `/upload`, `PATCH`, `PUT /content`, `DELETE` and
  `/reprocess` still let any member act on any folder or document in the org. (First-party
  *reads* — the list, including per-document viewer overrides, and `GET /{id}`, `/by-key`,
  `/content`, `/chunks`, `/summary`, `/logs` — now apply view masks; a member can always
  open an unfiled document they uploaded themselves.)
- **Facts from documents ingested before this release** have no masks recorded on their
  `Document` node. A claim's masks are the union of its sources' masks (see "Facts follow
  every source" below); a source with no recorded masks contributes the claim's current
  masks, which never widens anything but cannot narrow it either. Reprocessing a document,
  or any move/permission change on it, records its masks.
- **Legacy triplet graph context** (`_legacy_triplet_search`) admits a triplet when
  *either* endpoint vertex is visible, and vertex keys are a union across documents, so a
  relationship from a restricted document can surface through a vertex shared with a
  visible one. `/api/vector-chat` (v1 search/chat) always searches both shapes, but with
  `USE_FACT_ENGINE` on, ingest writes only reified claims (filtered per claim) and no new
  legacy triplets, so the exposure is limited to data ingested before the fact engine was
  enabled. Reprocessing such documents removes it.
- **Unassigned dimensions are `0`.** A member with no region asserts region `0`, so a
  region whose `permission_number` is `0` matches every member with no region. Give real
  dimensions non-zero numbers.
- **No per-record ACLs.** Access is by folder/document mask, per-entity write policy, and
  per-field read policy — there is no per-*record* owner/ACL model. Record-scoping patterns
  (e.g. the LMS `@me` filter) are conventions built in views/workflows, not an enforced
  RBAC primitive.

> Reviewed 2026-07-16 against Alembic migration 039. Source of truth is the code; if this doc disagrees with the code, the code wins.
