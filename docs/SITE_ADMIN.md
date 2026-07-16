# Site Admin Console

The Site Admin console is KM2's instance-wide, cross-organization operator surface:
one place for a superadmin to manage every organization, every user account, and the
platform's moving parts. It is distinct from the per-org **Admin** area (which a normal
org admin uses inside a single tenant). This doc is for operators running a KM2
deployment and engineers extending the admin surface.

## Table of Contents

- [Overview](#overview)
- [Access model](#access-model)
- [First-run bootstrap](#first-run-bootstrap)
- [The console and its tabs](#the-console-and-its-tabs)
- [Create-org wizard](#create-org-wizard)
- [Sent Emails (Mailpit)](#sent-emails-mailpit)
- [Deployments](#deployments)
- [Endpoints](#endpoints)
- [Cross-links](#cross-links)
- [Known gaps / TODO](#known-gaps--todo)

## Overview

Site Admin lives at the UI route `ui/src/app/(authenticated)/site-admin/page.tsx` and is
backed primarily by the API router `services/api/src/api/routers/admin.py` (mounted at
`/api/admin`). Some tabs call sibling routers that are also site-admin gated:
`routers/orgs.py` (`/api/orgs`) for org CRUD and `routers/memberships.py`
(`/api/memberships`, reached cross-org via the `X-Org-ID` header) for membership editing.

Everything here is cross-tenant: several admin endpoints run on a privileged DB session
that bypasses Row-Level Security by design, so one operator can see and act on every org
without holding a membership in it. That is precisely why access is tightly gated (below).

## Access model

Site-admin status is a single boolean on the user profile: `UserProfile.is_site_admin`
(the `is_site_admin` column on the `user_profiles` table). It is **not** per-org.

- **API gate.** The dependency `require_site_admin` in
  `services/api/src/api/auth/dependencies.py` reads the authenticated `CurrentUser` and
  raises `403 Site admin access required` unless `user.is_site_admin` is true. Every route
  in `admin.py` depends on it, and `orgs.py` applies it to create/update/delete.
- **UI gate.** `SiteAdminPage` reads `isSiteAdmin` from `useOrg()` (`OrgContext`) and
  renders "Site admin access required" for everyone else. This is a convenience only —
  the server is the real boundary.

How this differs from **org-admin**: org-admin is a per-membership flag
(`UserOrgMembership.is_org_admin`) enforced by `require_org_admin` within one org. A site
admin is treated as an implicit org admin of any org: `require_org_access` synthesizes an
elevated membership when a site admin has none, and sets
`is_org_admin = membership.is_org_admin or user.is_site_admin`. See [RBAC.md](RBAC.md) for
the full role/permission model and [AUTHENTICATION.md](AUTHENTICATION.md) for how the Clerk
JWT resolves to a `CurrentUser`.

Two safety rails on the `is_site_admin` / `is_active` flags (in `update_user_flags`):

- An admin cannot demote or deactivate **their own** account (`400`).
- The instance can never lose its **last active site admin**. A transaction-scoped Postgres
  advisory lock serializes concurrent demote/deactivate requests, and a count check returns
  `409 Cannot remove the last active site admin`.

## First-run bootstrap

On a fresh install there is no site admin, so there is nobody to open the console. KM2
bootstraps the first one with a one-time token (the Jupyter/Portainer pattern), implemented
in `services/api/src/api/services/setup_token.py` and `routers/setup.py` (`/api/setup`):

1. While no active site admin exists, the first worker to boot generates a URL-safe token,
   logs the **plaintext** to the server console, and stores only its SHA-256 hash in Redis
   under `setup:token:hash` (TTL-bound, never overwritten while unclaimed).
2. `GET /api/setup/status` is unauthenticated and returns `needs_setup` so the UI can show
   the wizard before anyone is an admin.
3. `POST /api/setup/claim` requires a signed-in Clerk user plus the token from the logs; it
   atomically consumes the token and sets `is_site_admin = true` on the caller. Thereafter
   new site admins are minted from the Users tab (`PATCH /api/admin/users/{id}`).

## The console and its tabs

`site-admin/page.tsx` defines exactly seven tabs. Each renders a component from
`ui/src/components/site-admin/`; per-tab help text lives in
`ui/src/lib/adminHelp.ts` (`SITE_ADMIN_TAB_HELP`).

### Organizations (`orgs`)

`OrgManager.tsx`. Lists every org on the instance and lets a site admin create, rename,
delete, and set an org's Home view. See [Create-org wizard](#create-org-wizard). Delete is
guarded by a type-the-name confirmation dialog because it is destructive and cross-store
(PostgreSQL cascade + Qdrant + Neo4j).
Backing endpoints: `GET/POST/PATCH/DELETE /api/orgs` (`routers/orgs.py`).

### Users (`users`)

`UserManager.tsx`. The full user list across all orgs, with substring search and paging.
Per user, toggle **site admin** (promote/demote) and **active** (activate/deactivate; a
deactivated user cannot sign in). The operator's own destructive buttons are disabled in the
UI, matching the server's self-protection rule.
Backing endpoints: `GET /api/admin/users`, `PATCH /api/admin/users/{profile_id}`.

### Memberships (`memberships`)

`GlobalMembershipManager.tsx`. Org-centric membership management "from the outside": pick any
org, then add/remove members and toggle org-admin without being a member yourself — useful to
bootstrap a new org or recover access when no one inside can. Fine-grained dimension
assignment (regions/roles/…) stays in the per-org Admin → Members tab.
It calls the org-scoped membership router with the target org in `X-Org-ID`:
`listUsersInOrg`, `getMembershipForUser`, `upsertMembership`, `updateMembership`,
`deleteMembership` → `routers/memberships.py`. (The admin router also exposes a *user*-centric
view, `GET /api/admin/users/{profile_id}/memberships` — every org one user belongs to — which
is not what this tab drives.)

### System (`system`)

`SystemStatus.tsx`. Health of the platform's moving parts for the whole deployment.
Backing endpoint: `GET /api/admin/system` (`system_status`) probes four components —
`database`, `redis`, `brain_api`, and `worker_queue` (Celery queue depth) — and reports each
as ok/error with latency, never raising.

### Celery (`celery`)

`CeleryMonitor.tsx`. Monitors the background task system that runs ingestion, workflow-outbox
sweeps, and other async jobs. Backing endpoint: `GET /api/admin/celery` (`celery_status`)
returns beat liveness + published schedule (read from the `celery:beat:heartbeat` /
`celery:beat:schedule` Redis keys the worker stamps), queued (pending) messages peeked from
the `celery` list, and tasks currently executing via a Celery `inspect().active()` broadcast
(with coarse ingest progress joined in). A `down` beat plus a non-draining queue is the cue
that scheduled work has stalled. Drill-ins: `POST /api/admin/jobs/{document_id}/cancel`
(cross-org cancel of an in-flight ingest) and `GET /api/admin/jobs/{document_id}/logs`
(the worker's capped per-document Redis log list).

### Sent Emails (`emails`)

`SentEmailsManager.tsx`. See [Sent Emails (Mailpit)](#sent-emails-mailpit).

### Deployments (`deployments`)

`DeploymentLogManager.tsx`. See [Deployments](#deployments).

## Create-org wizard

Creating an org is a single-step form in the Organizations tab (`OrgManager.tsx`), not a
multi-page wizard: enter a name and optional description, submit, and the org switcher
refreshes. Under the hood `POST /api/orgs/` (`create_org`, site-admin gated) does two things:

1. Inserts the org row (name, description, `use_knowledge_graph`).
2. Best-effort initializes the tenant in the knowledge stores via
   `BrainAPIClient.init_tenant(org_id)` — creating the org's Qdrant collections / Neo4j
   tenant. A failure here is logged, not fatal, so the org still gets created.

Renames and the Home-view setting go through `PATCH /api/orgs/{org_id}`; that same endpoint
also encrypts a per-org OpenAI key at rest when one is supplied.

## Sent Emails (Mailpit)

The Sent Emails tab inspects outgoing mail captured by the **Mailpit** container
(docker service `km2_mailpit`), so an operator can confirm what a workflow or form actually
sent. It is **dev/staging only**: in production the API talks to a real SMTP relay and
nothing is captured.

- **Why it lives under Site Admin:** Mailpit has no concept of organizations — it is a single
  global mail sink with no tenant scoping and no KM2 database involvement. There is no
  per-org way to expose it safely, so it is gated to site admins.
- **How it works:** `MailpitClient` (`services/api/src/api/services/mailpit_client.py`) is a
  thin async wrapper over Mailpit's REST API (`settings.mailpit_api_url`). The admin router
  proxies it: `GET /api/admin/emails` (list, newest first) and
  `GET /api/admin/emails/{message_id}` (full headers + text/HTML body; HTML is rendered in a
  fully sandboxed iframe so captured scripts can't run).
- **Unavailable is not an error:** when Mailpit is unreachable the client raises
  `MailpitUnavailableError`; the list endpoint returns `available=false` (not a 500) and the
  UI renders a friendly "Mailpit isn't running" state. The detail endpoint returns `502` if
  Mailpit is down or `404` if the message was evicted from Mailpit's ring buffer.

## Deployments

The Deployments tab is a read-only, cross-org audit log of the change-management release
system: every configuration promotion and rollback across all orgs, newest first, rendered by
`DeploymentLogManager.tsx`. Backing endpoint: `GET /api/admin/deployments` (`list_deployments`)
reads the `release_promotions` table on the privileged (RLS-bypass) session and joins release
and source/target org names; it exposes only release/target/org names, status, and strategy.

This tab is a window onto the release-promotion feature — org admins manage their own releases
under the change-management console. For the full model (releases, promotions, diffing,
targets, rollback) see [CHANGE_MANAGEMENT.md](CHANGE_MANAGEMENT.md).

## Endpoints

All routes below require `require_site_admin` unless noted. Admin-router paths are relative to
the `/api/admin` prefix; org and setup routes are shown with their full prefix.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/admin/users` | List all users across the instance (search + paging). |
| PATCH | `/api/admin/users/{profile_id}` | Promote/demote site admin; activate/deactivate account. |
| GET | `/api/admin/users/{profile_id}/memberships` | All orgs one user belongs to (user-centric). |
| GET | `/api/admin/system` | Component health: database, redis, brain_api, worker_queue. |
| GET | `/api/admin/celery` | Beat liveness + schedule, queued messages, active tasks. |
| POST | `/api/admin/jobs/{document_id}/cancel` | Cross-org cancel of an in-flight document ingest. |
| GET | `/api/admin/jobs/{document_id}/logs` | Per-document ingest log lines (from Redis). |
| GET | `/api/admin/emails` | List Mailpit-captured messages (dev/staging; `available=false` if down). |
| GET | `/api/admin/emails/{message_id}` | One captured message's headers + body. |
| GET | `/api/admin/deployments` | Cross-org promotion/rollback log (change management). |
| GET | `/api/orgs/` | List orgs (site admin sees all; others see their own). |
| POST | `/api/orgs/` | Create an org + init brain-api tenant. |
| GET | `/api/orgs/{org_id}` | Get one org (site admin or member). |
| PATCH | `/api/orgs/{org_id}` | Rename / set home view / set per-org OpenAI key. |
| DELETE | `/api/orgs/{org_id}` | Delete org; cascades PostgreSQL + Qdrant + Neo4j. |
| GET | `/api/setup/status` | **Unauthenticated** — whether the instance needs first-run setup. |
| POST | `/api/setup/claim` | Signed-in user + log token → become the first site admin. |

Request/response shapes live in `services/api/src/api/schemas/admin.py`,
`schemas/org.py`, and `schemas/setup.py` (the `DeploymentRow` model is defined inline in
`admin.py`).

## Cross-links

- [RBAC.md](RBAC.md) — roles, permissions, org-admin vs site-admin.
- [AUTHENTICATION.md](AUTHENTICATION.md) — Clerk JWT → `CurrentUser`, RLS session setup.
- [CHANGE_MANAGEMENT.md](CHANGE_MANAGEMENT.md) — releases and promotions behind the Deployments tab.
- [API.md](API.md) — REST surface and the versioned `/api/v1` public API.
- [DEPLOYMENT.md](DEPLOYMENT.md) — running the stack, including the Mailpit container.
- [README](../README.md) — project overview.

## Known gaps / TODO

- The **System** tab's "system settings" (per the tab help text) are surfaced only as the
  component-health probes of `GET /api/admin/system`; no writable instance-wide settings
  endpoint was found in `admin.py`.

> Reviewed 2026-07-16 against Alembic migration 039. Source of truth is the code; if this doc disagrees with the code, the code wins.
