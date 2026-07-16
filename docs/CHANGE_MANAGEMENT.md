# Change Management (Release Promotion)

Change management lets an org admin cut a **frozen release** of an organization's
configuration, move it through a review/approval gate, **promote** it to another
environment (another org in this database, or a remote KM2 instance), and **roll it
back** if the promotion goes wrong. This doc is for engineers and technical
evaluators working on the release-promotion feature; it grounds every claim in the
code under `services/api/src/api/services/migration/`, the `promotions`/`migration`
routers, and the `ui/src/components/change-management/` screens.

## Table of Contents

- [Overview](#overview)
- [Config lineage (migration 037)](#config-lineage-migration-037)
- [Release lifecycle & data model (migration 038)](#release-lifecycle--data-model-migration-038)
- [The bundle (v2)](#the-bundle-v2)
- [The diff engine](#the-diff-engine)
- [Promotion execution](#promotion-execution)
- [Targets](#targets)
- [REST API](#rest-api)
- [The screens](#the-screens)
- [Import / Export (sibling feature)](#import--export-sibling-feature)
- [Cross-links](#cross-links)
- [Known gaps / TODO](#known-gaps--todo)

## Overview

The feature ships in two migration phases:

- **Migration 037 (`config_lineage`)** gives every configurable object a durable
  cross-environment identity (`lineage_id`) so a promoted copy stays linked to its
  source across renames and re-imports.
- **Migration 038 (`release_management`)** adds the governance/data model:
  `releases`, `release_items`, `release_approvals`, `release_promotions`, and
  `promotion_targets`.

The control plane is the `PromotionService` (`services/api/src/api/services/promotion_service.py`),
which ties the bundle engine (exporter / importer / diff) to that governance model.
A promotion is deliberately built on the same **bundle** the org
Import/Export feature uses — change management is the governed, multi-environment
flow over that shared format.

The whole surface is org-admin only (Clerk session). A cross-org **local_org**
promotion additionally requires the caller to administer the target org, because
Postgres RLS cannot enforce a cross-tenant write on its own.

## Config lineage (migration 037)

`services/api/alembic/versions/037_config_lineage.py` adds a nullable
`lineage_id` UUID column to **14 config tables**, each with a partial unique index
`uq_<table>_lineage` on `(org_id, lineage_id) WHERE lineage_id IS NOT NULL`:

| Bundle section | Tables carrying `lineage_id` |
|---|---|
| entities | `entity_definitions`, `entity_fields`, `entity_relationships` |
| folders | `folders` |
| tags | `tags` |
| documents | `documents` |
| forms | `forms` |
| views | `views` |
| reports | `reports` |
| workflows | `workflows` |
| connections | `workflow_connections` |
| inbound_endpoints | `workflow_inbound_endpoints` |
| mcp_servers | `mcp_servers` |
| agents | `agents` |

Semantics (implemented in the exporter/importer, not the migration):

- `lineage_id IS NULL` means **self-origin** — the row was authored in this
  environment, so its lineage identity *is* its own `id`. Every pre-existing row is
  already correctly identified with **zero backfill**.
- The exporter emits `lineage_id := row.lineage_id or row.id` (`_lineage` in
  `exporter.py`). The importer/diff correlate an incoming object to an existing one
  by `(org_id, lineage_id)` first (rename-proof), falling back to the natural key
  (slug/name) on the first promotion, then stamp the lineage onto that natural-key
  match so subsequent promotions are lineage-linked.

Why this matters: without a stable identity, renaming a promoted object in either
environment would break the link and a later promotion would read as delete-old +
add-new. Lineage makes promotion identity stable across environments and renames.
No RLS/grant changes are needed — the new column lives on rows already `FORCE`
RLS-scoped by `org_id`.

## Release lifecycle & data model (migration 038)

`services/api/alembic/versions/038_release_management.py` creates five
tenant-scoped tables, all owned (and RLS-scoped) by the **source** org (`org_id` =
the org the release was cut from). Every table carries the hardened tenant-isolation
policies plus the `admin_bypass_all` policy (mirroring migration 034) so the
site-admin cross-org deployment log can read `release_promotions` on the bypass
session. Status columns are `varchar` + CHECK (never a Postgres enum), so the
vocabulary can evolve without a type migration. Models: `api/models/promotion.py`.

| Table | Purpose | Notable columns |
|---|---|---|
| `promotion_targets` | Where a release can be promoted | `kind`, `target_org_id` (local_org), `base_url`/`remote_org_id`/`secret_encrypted` (remote_instance), `enabled`, `config` |
| `releases` | Immutable frozen snapshot + governance state | `status`, `bundle` (inline JSONB) **or** `bundle_uri`, `bundle_hash`, `bundle_format_version`, `created_by_id`, `submitted_at`, `approved_at` |
| `release_items` | Normalized per-object inventory of a frozen release | `object_type`, `lineage_id`, `source_object_id`, `natural_key`, `fingerprint` |
| `release_approvals` | Append-only per-approver governance audit | `approver_id`, `decision`, `comment` (unique per `(release, approver)`) |
| `release_promotions` | One execution of a release against one target | `status`, `strategy`, `target_kind`/`target_label`/`target_org_id` (denormalized), `result_summary`, `pre_state_bundle` (reverse snapshot), `rollback_source_id`, `promoted_by_id`/`rolled_back_by_id` |

### Release statuses

Governance state machine, enforced by `PromotionService._require_status`. Constraint
`ck_release_status`; enum `ReleaseStatus`.

| Status | Meaning | Transitions from |
|---|---|---|
| `draft` | Just frozen; not submitted | (created) |
| `in_review` | Submitted, awaiting a decision | `draft`, `rejected` (via submit) |
| `approved` | Approved; promotable | `in_review` (via approve) |
| `rejected` | Changes requested; can be re-submitted | `in_review` (via reject) |
| `archived` | Defined in the model/CHECK; no transition wired yet |  |

Only an `approved` release can be promoted. A frozen release must have exactly one
bundle source (`bundle` xor `bundle_uri`), guarded by `ck_release_bundle_one`.

### Promotion statuses

Constraint `ck_release_promotion_status`; enum `PromotionStatus`.

| Status | Meaning |
|---|---|
| `pending` | Default column value on insert |
| `promoting` | Reserved for an in-progress apply |
| `promoted` | Applied successfully (the record is written at this status) |
| `failed` | Reserved for a failed apply |
| `rolled_back` | A later rollback re-applied the reverse snapshot |

In the current implementation a promotion is applied and its record inserted
atomically at `promoted`; `pending`/`promoting`/`failed` are defined in the
vocabulary but a live apply either commits `promoted` or raises (nothing persists).
A rollback flips the original record to `rolled_back` and inserts an inverse
`promoted` record whose `rollback_source_id` points at the record it undid.

## The bundle (v2)

The bundle is a plain JSON document — see `services/api/src/api/services/migration/bundle.py`
(`BUNDLE_KIND = "km2-migration-bundle"`, `BUNDLE_FORMAT_VERSION = 2`). Kept as
dicts (not tightly-versioned rows) so it stays readable, diffable, and
forward-compatible. `SUPPORTED_BUNDLE_FORMAT_VERSIONS = {1, 2}` — a v1 bundle simply
lacks `lineage_id` and the importer falls back to natural-key matching.

Resource sections, in dependency order (`RESOURCE_ORDER`, the order the importer must
follow so every reference target exists before its referrer):

```
tags, entities, connections, folders, workflows, inbound_endpoints,
reports, forms, views, mcp_servers, agents, records, documents
```

- **Config layer** — everything except `records`/`documents`. Round-trips cleanly.
- **Data layer** — `records` (custom-entity rows, per entity slug) and `documents`.
  Migrated best-effort.
- **v2 lineage** — every exported resource additionally carries
  `lineage_id = row.lineage_id or row.id`.
- **Secrets are never exported.** Connection credentials, webhook tokens, and a
  remote target's `secret_encrypted` are omitted; import regenerates or blanks them
  and reports freshly-minted webhook credentials once via `generated_secrets`
  (`GeneratedSecret`).
- **Size guard** — `MAX_BUNDLE_BYTES = 256 MB`, enforced on both the import route and
  the outbound push.

A **release** freezes a config-only export (`include_records=False,
include_documents=False`) inline into `releases.bundle`, records a canonical
`bundle_hash`, and indexes each config object into `release_items` with its
`lineage_id` and content `fingerprint` (`PromotionService._populate_items`).

## The diff engine

`services/api/src/api/services/migration/diff.py` (`compute_diff`) diffs a source
bundle against the target org's current-state export and classifies every config
object as **added / changed / unchanged / deleted**, grouped by resource type. This
powers the change-management preview.

- **Correlation** is by `lineage_id` first (rename-proof once linked), falling back
  to the natural key (slug/name) on the first promotion.
- **Change detection** is a content `fingerprint`: a SHA-256 of the exported dict
  with identity/volatile keys stripped (`id`, `lineage_id`, `created_at`,
  `updated_at`) and embedded cross-references normalized to *lineage* space, so a
  pure id-remap between environments does not read as a change.
- **Deletes** — a target object absent from the source is flagged `deleted` only if
  it is *promotion-managed* (`_is_promotion_managed`: its stored lineage differs from
  its own id), never a self-origin object the target authored. `manage_deletes_for`
  limits which types can report deletes; a forms-only release never marks the
  target's unrelated types for deletion. `delete_order` is the reverse dependency
  order deletes must apply in.
- **Records and documents are DATA, not config**: reported **count-only**
  (`count_only: true`, source row count as `added`) and never flagged for deletion.

`ObjectDiff` lists added/changed/deleted objects; unchanged objects are counted,
not listed. `BundleDiff.totals` carries the roll-up.

## Promotion execution

`services/api/src/api/services/migration/promotion.py` (`PromotionExecutor`) wraps
the exporter/importer/diff. It does all DB work but **never commits** — the caller
commits once, then calls `importer.dispatch_pending_ingests(...)` so document
ingestion is enqueued only after the rows are visible to the worker.

- **`preview(...)`** — exports the target's current config, diffs, and runs the
  importer in dry-run; the caller rolls the session back so nothing persists.
- **`promote(...)`** — captures a **reverse snapshot** of the target *before* any
  mutation (a full `km2-migration-bundle`, stored in
  `release_promotions.pre_state_bundle` for rollback), applies the bundle
  lineage-aware, then optionally runs guarded deletes. A normal forward promote
  limits delete management to the types the release actually carries
  (`_managed_delete_types`); rollback widens it to every config type
  (`full_delete_scope`).
- **`rollback(...)`** — re-applies the captured reverse snapshot with
  `OVERWRITE` + deletes on + `allow_data` + `override_inflight`, restoring
  overwritten objects and removing whatever the promotion created (including
  entities and their data). It is a deliberate, operator-confirmed corrective action.

### Deleter

`deleter.py` (`MigrationDeleter`) is the opt-in, config-layer deletion path,
deliberately separate from the additive importer. Objects are addressed by durable
`lineage_id` (never a mutable natural key) and deleted in **reverse dependency
order** (children before parents). It refuses to delete `folders`/`records`
(`_UNSUPPORTED`, no safe generic path — reported as warnings), and requires the
stronger `allow_data` gate for data-destructive types (`entities`, whose deletion
tears down the physical `ce_` table and its rows).

### In-flight guard

`inflight.py` (`InFlightGuard`) blocks *destructive* promotions that would delete an
object a live workflow run depends on. It finds non-terminal runs
(`pending`/`running`/`waiting`) that pin a to-be-deleted workflow, or that belong to
a workflow on a to-be-deleted entity, and raises `PromotionBlocked` (surfaced as
HTTP 409 with the blockers) unless the operator passes `override_inflight`.
Republishing a workflow is *not* blocked — a run pins its immutable
`workflow_version_id`, so an in-flight run finishes on the snapshot it started with.

### Redaction

`PromotionService._redact_summary` stores an audit blob (import counts, deleted
counts, diff totals) into `release_promotions.result_summary` with secret **values**
removed from `generated_secrets` — only `kind`/`name` survive. Live
`generated_secrets` are surfaced to the promoting admin once and are not persisted.

## Targets

`promotion_targets.kind` is one of two shapes (constraint `ck_promotion_target_shape`):

- **`local_org`** — another org in *this* database (`target_org_id`). The promotion
  runs the executor **in-process**; `PromotionService` scopes the apply to the target
  org via `db_scope.enter_tenant_owner` (RLS defense-in-depth over the explicit
  `org_id`) and the router enforces the caller is an admin of both orgs.
- **`remote_instance`** — another KM2 deployment (`base_url` + `remote_org_id` + a
  Fernet-encrypted `secret_encrypted` API key, encrypted with `ORG_ENCRYPTION_KEY`
  exactly like a workflow connection, never returned or exported).

### Outbound push (SSRF-guarded)

The remote leg is `transport.py` (`OutboundPushClient`). It reuses the same
deny-by-default SSRF allow-list the workflow webhook actions use
(`assert_outbound_host_allowed`: allow-list + private-IP block), requires **HTTPS**
(except for explicitly trusted local hosts), and size-caps the payload at
`MAX_BUNDLE_BYTES`. `ping(...)` probes reachability + the key's config access; a
`409` from the remote surfaces as `PromotionBlocked`, and a `401/403` as
`TransportError` ("missing config:write"). `PromotionService` **closes the DB session
before the network call** so an up-to-300s push does not hold a pooled connection
idle-in-transaction.

### Inbound receiver (`/api/v1/config`)

The *remote* side is `services/api/src/api/routers/v1/config.py`, mounted under
`/api/v1/config`, authenticated by an **org API key** (not a Clerk session):

- `GET /api/v1/config/ping` — requires scope **`config:read`**; returns
  `bundle_format_version` so the source can warn on a mismatch.
- `POST /api/v1/config/promotions` — requires scope **`config:write`**; applies the
  pushed bundle to the key's org via `PromotionExecutor` on the DDL-capable,
  still-RLS-scoped owner session (`get_apikey_tenant_owner_db`), returning the result
  including the reverse snapshot.

### Scopes: `config:read` / `config:write`

Defined in `services/api/src/api/services/api_key_scopes.py`:

- `config:read` — "Read instance/config info (e.g. verify a promotion connection)."
- `config:write` — "Receive and apply configuration promotions." It can
  create/update/delete entities, workflows, and agents, so it is listed in
  `SENSITIVE_SCOPES` and is **never satisfied by a wildcard grant** (`*` or
  `config:*`) — a key must be minted with `config:write` explicitly (`has_scope`
  short-circuits sensitive scopes before any wildcard check). See [RBAC.md](RBAC.md)
  and [API.md](API.md) for the full scope model and how org API keys are minted.

## REST API

All `/api/promotions` and `/api/migration` routes are org-admin only (Clerk session,
`require_org_admin`). See [API.md](API.md) for request/response schemas.

### Change-management control plane — `services/api/src/api/routers/promotions.py` (`/api/promotions`)

| Method | Path | Purpose | Permission |
|---|---|---|---|
| GET | `/api/promotions/targets` | List promotion targets | org admin |
| POST | `/api/promotions/targets` | Create a target | org admin (+ admin of target org for `local_org`) |
| POST | `/api/promotions/targets/{id}/test` | Probe a remote target (reachability + key config access) | org admin |
| DELETE | `/api/promotions/targets/{id}` | Delete a target | org admin |
| GET | `/api/promotions/releases` | List releases | org admin |
| POST | `/api/promotions/releases` | Freeze a selection into a new draft release | org admin |
| GET | `/api/promotions/releases/{id}` | Release detail (items, approvals, promotions) | org admin |
| POST | `/api/promotions/releases/{id}/submit` | draft/rejected → in_review | org admin |
| POST | `/api/promotions/releases/{id}/approve` | in_review → approved | org admin |
| POST | `/api/promotions/releases/{id}/reject` | in_review → rejected | org admin |
| POST | `/api/promotions/releases/{id}/diff` | Preview diff against a target (read-only) | org admin (+ target-org admin for `local_org`) |
| POST | `/api/promotions/releases/{id}/promote` | Apply an approved release to a target | org admin (+ target-org admin for `local_org`) |
| GET | `/api/promotions` | Promotion history (optional `release_id`) | org admin |
| POST | `/api/promotions/{promotion_id}/rollback` | Roll a promoted promotion back | org admin (+ target-org admin for `local_org`) |

A destructive `promote` blocked by in-flight runs returns **409** with the blockers;
retry with `override_inflight=true`.

### Org portability — `services/api/src/api/routers/migration.py` (`/api/migration`)

| Method | Path | Purpose | Permission |
|---|---|---|---|
| GET | `/api/migration/manifest` | Lightweight index (ids + names) of selectable objects | org admin |
| POST | `/api/migration/export` | Serialize the org (or a selection) to a downloadable bundle | org admin |
| POST | `/api/migration/diff` | Preview what importing an uploaded bundle would do | org admin |
| POST | `/api/migration/import` | Rebuild a bundle into the current org (`strategy`, `dry_run`, `selection`) | org admin |

### Inbound config receiver — `services/api/src/api/routers/v1/config.py` (`/api/v1/config`)

| Method | Path | Purpose | Permission |
|---|---|---|---|
| GET | `/api/v1/config/ping` | Authenticated probe + bundle format version | API key `config:read` |
| POST | `/api/v1/config/promotions` | Apply a pushed bundle to the key's org | API key `config:write` |

### Cross-org deployment log — `services/api/src/api/routers/admin.py`

| Method | Path | Purpose | Permission |
|---|---|---|---|
| GET | `/api/admin/deployments` | Every promotion across all orgs (newest first) | site admin |

## The screens

### `/change-management` console

Route `ui/src/app/(authenticated)/change-management/page.tsx` renders
`ChangeManagementConsole` (`ui/src/components/change-management/ChangeManagementConsole.tsx`),
org-admin gated, with two tabs:

- **Releases** — lists releases with a `StatusBadge`; "New release" opens a form that
  loads the export manifest and reuses the Import/Export `ResourceSelector` to pick
  which objects to freeze, then calls `createRelease`.
- **Targets** — add a `local_org` target (a picker of other orgs the caller
  administers) or a `remote_instance` target (base URL + remote org id + `config:write`
  API key, stored write-only). Remote targets get a **Test** button (`testTarget`).

Opening a release renders `ReleaseDetailView`
(`ui/src/components/change-management/ReleaseDetailView.tsx`):

- **Governance** — Submit for review / Approve / Request changes buttons keyed to the
  release status; a "Frozen contents" summary of `release_items` by type; the approval
  trail.
- **Promote panel** (only when `approved`) — choose a target + collision strategy
  (defaults to **overwrite**), **Preview diff** (`diffRelease`, run with
  `apply_deletes: true`, rendered by `DiffTable` with a deletion warning banner), then
  **Promote** (`promoteRelease`). A 409 shows the in-flight blockers with an override
  checkbox; freshly-minted webhook credentials are shown once.
- **Promotion history** — each promotion with its status; a `promoted`
  (non-rollback) row offers a confirm-gated **Roll back** (`rollbackPromotion`).

Shared bits live in `ui/src/components/change-management/parts.tsx` (`StatusBadge`,
`DiffTable`). API client: `ui/src/lib/api/promotions.ts`.

### Site Admin → Deployments

`ui/src/app/(authenticated)/site-admin/page.tsx` has a **Deployments** tab rendering
`ui/src/components/site-admin/DeploymentLogManager.tsx`, which calls
`GET /api/admin/deployments` (`ui/src/lib/api/deployments.ts`) to show every config
promotion across all orgs (when / release / from / to / status), newest first. It is
site-admin only and reads `release_promotions` cross-org on the bypass session.

## Import / Export (sibling feature)

Import/Export is the ungoverned, single-flow sibling that shares the same bundle
format and engine. Route `ui/src/app/(authenticated)/import-export/page.tsx` renders
`ImportExportConsole` (`ui/src/components/import-export/`) with Export and Import
tabs; the same console is also embedded as an Admin-console tab. API client:
`ui/src/lib/api/migration.ts`.

- **Export** (`POST /api/migration/export`) serializes the org — optionally narrowed
  by a `Selection` (a `{resource_type: id[]}` object; a type omitted means "all of
  it") chosen with `ResourceSelector` over the `/api/migration/manifest` index — to a
  downloadable JSON `km2-migration-bundle`. Secrets are never included.
- **Import** (`POST /api/migration/import`) rebuilds a bundle into the *current* org
  with a **collision strategy** (`CollisionStrategy`): `skip` (leave the existing
  object, map references to it), `overwrite` (update in place), or `rename` (create a
  suffixed copy). `dry_run=true` runs the whole import in a rolled-back transaction
  and returns the `ImportSummary` without persisting. Cross-references embedded in
  form/view configs and workflow graphs are id-remapped to the newly-created rows
  (`remap_refs` / `IdMap`); unmapped references dangle with a warning rather than
  failing the import.

Relationship to change management: same bundle, different flow. Import/Export moves a
bundle in/out of the *current* org by hand (no release, no approval, no target, no
rollback). Change management freezes that bundle into a governed, reviewed **release**
and applies it to a **target** environment with a reverse snapshot for rollback. The
release-creation form even reuses the Import/Export `ResourceSelector` to pick what to
freeze.

## Cross-links

- [API.md](API.md) — full endpoint schemas and the org-API-key / scope model.
- [AUTHENTICATION.md](AUTHENTICATION.md) — Clerk sessions, org API keys, and the
  `config:read` / `config:write` scope rules.
- [DATABASE.md](DATABASE.md) — schema, RLS policies, and migration references.
- [RBAC.md](RBAC.md) — org-admin / site-admin roles and API-key scopes.
- [SITE_ADMIN.md](SITE_ADMIN.md) — the cross-org Deployments log in the Site Admin console.
- [README](../README.md) — repository overview.

## Known gaps / TODO

- `release.status = 'archived'` and promotion statuses `pending` / `promoting` /
  `failed` exist in the model + CHECK vocabulary but have no transition wired in
  `PromotionService` yet (a live apply commits `promoted` or raises).
- `releases.bundle_uri` / `release_promotions.pre_state_uri` (object-store bundle
  references for record/doc-heavy releases) exist in the schema; the current service
  stores bundles inline in `bundle` / `pre_state_bundle`.
- `X-Idempotency-Key` is accepted by the inbound receiver but enforced de-duplication
  is a noted hardening follow-up (config apply with skip/overwrite is naturally
  idempotent).

> Reviewed 2026-07-16 against Alembic migration 039. Source of truth is the code; if this doc disagrees with the code, the code wins.
