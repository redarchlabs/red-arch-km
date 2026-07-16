# Authentication

How KM2 identifies callers and binds them to an organization. This is the living
reference for engineers and technical evaluators; it consolidates auth details that
were previously scattered across routers, dependencies, migrations, and the UI. The
historical migration plan lives in `docs/design/clerk-auth-migration-spec.md` and is
superseded by this document.

KM2 has two authentication surfaces plus two credential types for machines:

- **Interactive user sessions** — a browser signs in with **Clerk** (cloud OIDC) and
  presents a short-lived JWT that the backend verifies per request.
- **Machine / API access** — external callers present an **org API key** (`km2_…`) to
  the versioned public surface `/api/v1`.
- **Signed inbound webhooks** — external senders trigger a workflow with an
  HMAC-signed `POST /api/inbound/{token}`.
- **Connection credentials** — outbound workflow actions authenticate to third-party
  systems (e.g. a robot bridge) with a stored, encrypted credential.

Authorization (who may do what *within* an org) is a separate concern — the 32-bit
`access_mask` RBAC model, computed entirely from the database and never from IdP
claims. See [RBAC.md](RBAC.md).

## Table of Contents

- [1. Overview](#1-overview)
- [2. User authentication flow (Clerk)](#2-user-authentication-flow-clerk)
- [3. Authorization → Postgres RLS](#3-authorization--postgres-rls)
- [4. Org membership & multi-tenancy](#4-org-membership--multi-tenancy)
- [5. API-key authentication (`/api/v1`)](#5-api-key-authentication-apiv1)
- [6. Inbound webhook signing](#6-inbound-webhook-signing)
- [7. Connection credentials (outbound)](#7-connection-credentials-outbound)
- [8. Service-to-service auth](#8-service-to-service-auth)
- [9. Dev/test auth (E2E header bypass)](#9-devtest-auth-e2e-header-bypass)
- [10. Environment variables](#10-environment-variables)
- [11. Security notes](#11-security-notes)
- [12. Cross-links](#12-cross-links)
- [Known gaps / TODO](#known-gaps--todo)

## 1. Overview

Clerk is the **sole** identity provider (it replaced self-hosted Keycloak; the
`user_profiles.keycloak_sub` column was renamed to `auth_subject` in migration
`003_rename_keycloak_sub_to_auth_subject`). The backend is a **stateless verifier**:
it issues no tokens and has no login/refresh/logout endpoints — the UI talks to Clerk
directly, and the only server-side identity route is `GET /api/auth/me`
(`services/api/src/api/routers/auth.py`).

The Python `services/api` service (port 8000) is the live, CI-covered backend and the
authority for everything documented here. The in-progress Go rewrite
(`services/api-go`) mirrors the same verification model; `services/api/src/api/auth/clerk.py`
is written to stay in parity with the Go verifier (`internal/middleware/auth.go`). See
ARCHITECTURE.md for Go migration status.

| Surface | Credential | Verified in | Sets tenant context |
|---|---|---|---|
| Browser / first-party UI | Clerk JWT (`Authorization: Bearer`) + `X-Org-ID` | `auth/clerk.py`, `auth/dependencies.py` | `get_tenant_db` (RLS) |
| Public `/api/v1` | Org API key (`km2_…`) | `auth/api_key.py` | `get_apikey_tenant_db` (RLS) |
| Inbound webhook | HMAC `X-KM2-Signature` over per-endpoint secret | `services/workflow/webhook_signing.py` | endpoint's org |
| Outbound connection | Stored encrypted secret → request header | `services/workflow/actions.py` | n/a (outbound) |
| Worker → API callback | Shared secret `X-Internal-API-Key` | `auth/dependencies.py` `require_internal_api_key` | n/a |
| Dev/test only | `X-Test-User` + `X-Test-Secret` | `auth/dependencies.py` (gated) | via user's `X-Org-ID` |

## 2. User authentication flow (Clerk)

### 2.1 Sign-in (UI)

The Next.js app (`ui/`) wraps the tree in `<ClerkProvider>` in `ui/src/app/layout.tsx`
(`signInUrl="/login"`, `signUpUrl="/sign-up"`, `afterSignOutUrl="/login"`). Route
protection is enforced by `ui/src/middleware.ts` (`clerkMiddleware()` +
`createRouteMatcher`): every route calls `auth.protect()` except the public matcher
(`/`, `/help(.*)`, `/login(.*)`, `/sign-up(.*)`, `/intake(.*)`). Sign-in and sign-up
render Clerk's `<SignIn/>` / `<SignUp/>` components at `/login` and `/sign-up`. Logout
calls Clerk `signOut({ redirectUrl: "/login" })` after clearing the current-org key
(`ui/src/components/nav/Header.tsx`, `ui/src/context/AuthContext.tsx`).

### 2.2 Token → API

Clerk session tokens are short-lived (~60s) and auto-refreshed by the Clerk SDK; the
UI attaches a fresh one on every call. Because the UI (`:3000`) and API (`:8000`) are
cross-origin, the token travels as a bearer header, not a cookie:

- `ui/src/lib/auth/clerk.ts` `getToken()` reads `window.Clerk.session.getToken(...)`,
  passing the JWT template named by `NEXT_PUBLIC_CLERK_JWT_TEMPLATE` (**`redarch-km`**).
  The template must emit `email` and `username` claims — Clerk's default session token
  omits them, and the backend provisions on those claims.
- `ui/src/lib/api/client.ts` (axios interceptor) sets `Authorization: Bearer <token>`
  and `X-Org-ID` (from `localStorage["redarch:currentOrgId"]`). Streaming calls
  (`ui/src/lib/api/search.ts`, `runStream.ts`) attach the same two headers via `fetch`.
  A `401` response redirects to `/login`.

### 2.3 Per-request verification (backend)

`get_current_user` (`services/api/src/api/auth/dependencies.py`) is the FastAPI
dependency behind every user-authenticated route:

1. If `e2e_test_mode` is on **and** an `X-Test-User` header is present, take the dev/test
   bypass (§9). Otherwise a bearer token is required.
2. `_verify_bearer_token` reads the token's `iss` **unverified** only to select the
   verifier; the token's `iss` must equal the configured `CLERK_JWT_ISSUER`, and the
   verified decode re-pins the issuer independently, so a forged `iss` cannot bypass
   validation.
3. `validate_clerk_token` (`auth/clerk.py`) performs the real check:
   - RS256, with the signing key matched by `kid` against the Clerk JWKS fetched from
     `{issuer}/.well-known/jwks.json` (cached 300s in-process).
   - Issuer pinned to `CLERK_JWT_ISSUER`.
   - **No `aud`.** Clerk default tokens carry no audience; the security-critical
     replacement is a **default-deny `azp` check** — the token's `azp` (authorized
     party = the UI origin) must be a non-empty member of `CLERK_ALLOWED_AZP`. A
     missing/empty/foreign `azp` is rejected (anti token-origin-confusion). Config
     fails fast at startup if `CLERK_JWT_ISSUER` is set without `CLERK_ALLOWED_AZP`.
4. Claims consumed: `sub` (required), `username` (falling back to `preferred_username`),
   `email`. `provision_user_from_claims` (`services/user_provisioning.py`) finds or
   creates the `user_profiles` row keyed by `auth_subject = sub`, syncing username/email;
   a first-time user is created with `is_site_admin = false` and **no** membership (an
   admin grants access later). Missing username/email fall back to `sub`-derived values.
5. `_ensure_active` rejects a deactivated profile with `403` even for a valid token.

The result is an immutable `CurrentUser` (`sub`, `username`, `email`, `profile_id`,
`is_site_admin`). Any verification failure returns an opaque `401` ("Invalid or expired
token").

## 3. Authorization → Postgres RLS

A verified identity is bound to a Postgres tenant context so Row-Level Security
enforces isolation as a database-level backstop, independent of application filtering.
This is the **two-layer** model: RLS scoping **plus** tenant-bound repositories that
also carry an explicit `org_id`. The `SET LOCAL` helpers live in one place,
`services/api/src/api/db_scope.py`.

The app connects as the non-superuser role **`km_app`** (granted by migration
`035_grant_km_app_role`), which is fully subject to `FORCE ROW LEVEL SECURITY`. Two
request modes:

| Mode | Dependency | `db_scope` call | Effect |
|---|---|---|---|
| Tenant-scoped | `get_tenant_db` | `enter_tenant(session, org_id)` | `SET LOCAL ROLE app_user`; `app.bypass = 'off'`; `set_config('app.current_tenant_id', org_id, true)` — RLS scopes every statement to one org |
| Cross-org bypass | `get_db` | `enter_bypass(session)` | `SET LOCAL app.bypass = 'on'` — the permissive `admin_bypass_all` policy (migration `034_admin_bypass_policies`) widens visibility to every org |

`get_tenant_db` (`services/api/src/api/dependencies.py`) resolves the org from the
required `X-Org-ID` header (`get_org_id`, validated as a UUID), drops to `app_user`,
pins the tenant GUC, and adds `SET LOCAL TIME ZONE 'UTC'` + `statement_timeout = '30s'`.
Everything is transaction-local (`SET LOCAL` / `set_config(..., true)`), so pooled
connections auto-revert on commit/rollback. A session that sets *neither* mode fails
closed (RLS with no tenant GUC returns zero rows). The `app_user` role itself comes
from migration `007_ensure_app_user_role`.

For the full RLS policy template and the list of RLS-enabled tables, see
[DATABASE.md](DATABASE.md#row-level-security). For how `access_mask` gates records
within an org, see [RBAC.md](RBAC.md).

## 4. Org membership & multi-tenancy

A user maps to zero or more orgs through `user_org_memberships`. The membership-aware
dependencies (`services/api/src/api/auth/dependencies.py`) layer on top of
`get_current_user`:

| Dependency | Requires | Notes |
|---|---|---|
| `require_org_access` | a membership in the `X-Org-ID` org | Returns `OrgContext` (user, org_id, membership, `is_org_admin`). A **site admin** with no explicit membership gets a synthetic org-admin membership. |
| `require_org_admin` | `is_org_admin` in the current org | 403 otherwise. |
| `require_site_admin` | `CurrentUser.is_site_admin` | Cross-org site operations. |

The **current org is chosen client-side** by the `X-Org-ID` header, not encoded in the
Clerk token — Clerk supplies identity only, and KM2 keeps its own orgs + RBAC. In the
UI, `ui/src/context/OrgContext.tsx` (`OrgProvider`, nested inside `ClerkProvider`)
persists the selection in `localStorage["redarch:currentOrgId"]`, validates it against
the user's accessible orgs (else falls back to the first), and re-persists it. Users
switch orgs via `ui/src/components/nav/OrgSwitcher.tsx`; the site-admin console can
override per request by passing an explicit `X-Org-ID`.

## 5. API-key authentication (`/api/v1`)

External/enterprise callers cannot present a Clerk browser JWT, so the versioned public
surface `/api/v1` is authenticated by **org API keys**. See [API.md](API.md#enterprise-api--apiv1-org-api-key)
for the endpoint catalog.

### 5.1 Creation

Keys are managed from the Admin Area **API & Keys** tab, which calls the Clerk-authed,
org-admin-gated router `services/api/src/api/routers/api_keys.py` (mounted at
`/api/api-keys`):

| Method + path | Auth | Purpose |
|---|---|---|
| `GET /api/api-keys/scopes` | `require_org_admin` | Scope catalog for the create form. |
| `GET /api/api-keys/` | `require_org_admin` | List keys (metadata only). |
| `POST /api/api-keys/` | `require_org_admin` | Mint a key; the plaintext is returned **once**. |
| `DELETE /api/api-keys/{id}` | `require_org_admin` | Revoke immediately (idempotent). |

### 5.2 Storage (shown once, hashed at rest)

Minting (`services/api/src/api/services/api_key_service.py` `generate_key`):

- Format `km2_<token>`, where `<token>` is `secrets.token_urlsafe(32)`.
- Only the **SHA-256 hex digest** (`hash_key`) is stored in `api_keys.key_hash`; the
  plaintext is returned to the admin exactly once and never persisted or logged.
- A non-secret `key_prefix` (first 12 chars, e.g. `km2_AbC12xyz`) is stored so the admin
  list can recognise a key without revealing it.
- Up to `MAX_KEYS_PER_ORG = 100` keys per org.

The table (`services/api/alembic/versions/028_api_keys.py`) is RLS-forced with the
standard per-tenant policy; `key_hash` is globally unique so the auth path resolves a
presented key to exactly one row across all tenants.

### 5.3 Authentication path

`require_api_key` (`services/api/src/api/auth/api_key.py`) reads the key from
`Authorization: Bearer km2_…` **or** the `X-API-Key` header, re-hashes it, and looks it
up by `key_hash` on a dedicated **short-lived privileged** session (cross-tenant, before
any org is known). It rejects unknown / revoked (`revoked_at`) / expired (`is_expired`)
keys, all with an opaque `401` (no oracle for which check tripped). A debounced
`last_used_at` touch (≥60s apart) runs on that session and commits *before* the endpoint
runs, so a hot key never holds a row lock across a request. `require_scope(scope)` then
gates each endpoint, and `get_apikey_tenant_db` opens the RLS session scoped to the key's
org (same `enter_tenant` mechanics as §3, org from the key rather than a header).

### 5.4 Scopes

A scope is a `"<domain>:<action>"` string. Endpoints declare the single concrete scope
they require; keys are minted with an explicit subset. Catalog
(`services/api/src/api/services/api_key_scopes.py` `API_SCOPES`):

| Scope | Grants |
|---|---|
| `entities:read` | List/read custom entity definitions (schema). |
| `records:read` | List/read entity records. |
| `records:write` | Create/update/delete entity records. |
| `reports:read` | List/read saved reports. |
| `reports:run` | Execute reports and ad-hoc aggregations. |
| `workflows:read` | List workflows, inspect runs. |
| `workflows:run` | Trigger any workflow run (**high privilege** — bypasses per-workflow run permission). |
| `search:read` | Semantic search + RAG chat over the knowledge base. |
| `knowledge:read` | List/read documents and folders. |
| `agents:read` | List agents, inspect agent runs. |
| `agents:run` | Trigger an agent run (**high privilege**). |
| `work_orders:read` | List/read work orders. |
| `work_orders:write` | File/update work orders. |
| `config:read` | Read instance/config info (e.g. verify a promotion connection). |
| `config:write` | Receive and apply configuration promotions (**very high privilege**). |

Wildcards `"*"` (everything) and `"<domain>:*"` (a whole domain) are honored when a key
is *granted* a scope. **Exception:** `config:write` is a `SENSITIVE_SCOPE` — it is
**never** satisfied by a wildcard and must be listed explicitly, because it can
remote-control an org's entire configuration. `has_scope` enforces this; `normalize_scopes`
rejects unknown scopes at mint time. `config:read` / `config:write` protect the inbound
config-promotion receiver used by release promotion — see CHANGE_MANAGEMENT.md.

### 5.5 Rate limits

The `/api/v1` router (`services/api/src/api/routers/v1/__init__.py`) applies two
limiters to **every** route, in order. First, a coarse **per-IP** pre-auth throttle
(`enforce_ip_rate_limit`, `API_IP_RATE_LIMIT_PER_MINUTE`, default 1200) that runs
*before* key resolution, so floods of missing/invalid keys can't hammer the auth
lookup. Then a **per-key** Redis quota (`enforce_api_rate_limit`,
`API_KEY_RATE_LIMIT_PER_MINUTE`, default 600), which also resolves the key principal;
it sets `X-RateLimit-Limit` / `X-RateLimit-Remaining` and returns `429` with
`Retry-After` when exhausted. `config:write` uses a
DDL-capable session (`get_apikey_tenant_owner_db`, `km_app` role, 300s statement timeout)
because applying a promotion runs `ce_*` entity-table DDL; RLS still enforces.

## 6. Inbound webhook signing

`POST /api/inbound/{token}` (`services/api/src/api/routers/inbound.py`) starts and runs
inline the workflow bound to that token, with the JSON body as the run's input. The
opaque path token selects the endpoint (only its hash is stored, like an API key). When
the endpoint has a signing secret, a valid HMAC signature is **also** required, so an
exposed URL is not on its own an abuse surface.

The scheme (`services/api/src/api/services/workflow/webhook_signing.py`) is the same one
Stripe and GitHub use:

```
X-KM2-Signature: t=<unix_seconds>,v1=<hex hmac-sha256>
```

- The signed message is `"{t}.{raw_body}"` over the **exact raw request bytes** (HMAC-SHA256
  keyed by the endpoint secret). The receiver reads the raw bytes, recomputes, and compares
  in constant time.
- **Replay protection:** a timestamp outside `DEFAULT_TOLERANCE_SECONDS = 300` is rejected,
  bounding a captured-request replay window. (The scheme is timestamp-tolerant, not
  nonce-tracked — within the window an identical replay would re-verify.)
- The per-endpoint secret is high-entropy, returned **once** when the endpoint is created,
  and stored **Fernet-encrypted** (`workflow_inbound_endpoints.signing_secret_encrypted`,
  migration `022_inbound_signing_secret`, keyed by `ORG_ENCRYPTION_KEY`) because the
  receiver must recover it to recompute the HMAC.

Any signature failure (missing, malformed, stale, mismatched) maps to a single opaque
`401`; an unknown/disabled endpoint returns `404`. The token lookup runs on a privileged
cross-tenant session, then downgrades to the endpoint's org before any write. See
[WORKFLOW_ENGINE.md](WORKFLOW_ENGINE.md) for how the run executes and MCP_AND_INTEGRATIONS.md
for the integration surface.

## 7. Connection credentials (outbound)

Workflow "connections" store credentials for **outbound** calls (e.g. a robot-control
bridge). Model `WorkflowConnection` (`services/api/src/api/models/workflow.py`), table
`workflow_connections` (migration `019_workflow_connections`, RLS-forced). The secret is
Fernet-encrypted at rest (`secret_encrypted`, keyed by `ORG_ENCRYPTION_KEY`) and decrypted
only at execute time into a non-serialized `ResolvedConnection`; it is never written to a
step output, input snapshot, or log. Any read endpoint exposes only `has_secret: bool`.

Supported `auth_type` values (`CONNECTION_AUTH_TYPES`) and how `_auth_headers`
(`services/api/src/api/services/workflow/actions.py`) attaches them:

| `auth_type` | Header sent |
|---|---|
| `none` | (no auth header) |
| `bearer` | `Authorization: Bearer <secret>` |
| `api_key` | `<config.header>: <secret>` — header name from `config["header"]`, default `X-API-Key` |
| `basic` | `Authorization: Basic base64(config["username"]:<secret>)` |

The **robot** case is an `api_key` connection with `config = {"header": "X-KM2-Command-Key"}`,
so the workflow sends `X-KM2-Command-Key: <secret>`. Outbound calls
(`http_request` action and the form/agent `call_connection` path,
`POST /api/workflows/connections/call`) pass the same deny-by-default SSRF guard
`assert_outbound_host_allowed`: the host must be in `WORKFLOW_WEBHOOK_ALLOWLIST` (or in
`WORKFLOW_TRUSTED_LOCAL_HOSTS`, which additionally bypasses the private-IP block to reach
a LAN/localhost robot bridge), scheme `http`/`https` only. Connection CRUD
(`/api/workflows/connections`) is gated by `require_org_admin`; invoking a connection
(`/connections/call`) is gated by `require_org_access` (any member) and still injects the
secret server-side.

## 8. Service-to-service auth

Backend services authenticate to each other with **shared secrets**, not Clerk:

| Direction | Header | Secret | Verified by |
|---|---|---|---|
| api / worker → brain-api | `X-API-Key` | `BRAIN_API_KEY` | brain-api |
| worker → api callbacks | `X-Internal-API-Key` | `INTERNAL_API_KEY` | `require_internal_api_key` (`auth/dependencies.py`) |

`require_internal_api_key` compares in constant time (`hmac.compare_digest`) and, if the
configured secret is empty, returns `503` (disabled) rather than allowing anonymous
access. The two secrets are deliberately distinct so compromise of one does not grant the
other.

## 9. Dev/test auth (E2E header bypass)

For automated tests only, the API accepts a header pair in place of a Clerk JWT
(`_resolve_e2e_user` in `services/api/src/api/auth/dependencies.py`):

- `X-Test-User: <username>:<email>` + `X-Test-Secret: <shared secret>`.
- Active **only** when `API_E2E_TEST_MODE=true` **and** the presented secret matches
  `API_E2E_TEST_SECRET`. Both conditions are required; it is never active in production.
- The user is auto-provisioned in Postgres on first request (subject `e2e-<username>`),
  so no Clerk users are needed. Org, dimensions, and the `e2e_admin` site admin are seeded
  by `services/api/scripts/seed_e2e.py`.

The Playwright and pytest suites use this path; Clerk is only needed for the UI to boot.
Full recipe: [testing/e2e-seeded-auth.md](testing/e2e-seeded-auth.md). **Never enable
`API_E2E_TEST_MODE` in production** — it would let anyone holding the shared secret assume
any identity.

## 10. Environment variables

### Backend (`services/api`, from `.env` — see `.env.example`)

| Var | Purpose |
|---|---|
| `CLERK_JWT_ISSUER` | Clerk Frontend API URL; the token's `iss` must match it. Empty ⇒ Clerk verify disabled. |
| `CLERK_ALLOWED_AZP` | Comma-separated allowlist of UI origins (authorized parties). **Required** when `CLERK_JWT_ISSUER` is set; must match Clerk's emitted `azp` byte-for-byte. |
| `CLERK_SECRET_KEY` | Clerk Backend API secret (`sk_…`). Reserved for Backend-API provisioning; not needed for JWKS verify. |
| `API_KEY_RATE_LIMIT_PER_MINUTE` | Per-key `/api/v1` quota (default 600). |
| `API_IP_RATE_LIMIT_PER_MINUTE` | Per-IP pre-auth quota on `/api/v1` (default 1200). |
| `API_DOCS_ENABLED` | Serve `/api/v1/docs` (default true). |
| `ORG_ENCRYPTION_KEY` | Fernet key for per-org secrets at rest (connection + webhook signing secrets, org provider keys). **Required in production**; a dev default warns at startup. |
| `BRAIN_API_KEY` | Shared secret, api/worker → brain-api (`X-API-Key`). |
| `INTERNAL_API_KEY` | Shared secret, worker → api callbacks (`X-Internal-API-Key`). |
| `WORKFLOW_WEBHOOK_ALLOWLIST` | SSRF allowlist for outbound workflow hosts (empty ⇒ disabled). |
| `WORKFLOW_TRUSTED_LOCAL_HOSTS` | Hosts allowed to bypass the private-IP SSRF block (e.g. a robot bridge). |
| `API_CORS_ORIGINS` | Allowed browser origins (must include the UI origin for cross-origin bearer calls). |
| `API_E2E_TEST_MODE` | Dev/test only — enables the `X-Test-User` bypass. Never in prod. |
| `API_E2E_TEST_SECRET` | Shared secret required alongside `X-Test-User`. |

### Frontend (`ui/`, from `ui/.env.local` — see `ui/.env.local.example`)

| Var | Purpose |
|---|---|
| `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` | Clerk publishable key (`pk_…`), exposed to the browser. |
| `CLERK_SECRET_KEY` | Read server-side by `@clerk/nextjs` (middleware/SSR). |
| `NEXT_PUBLIC_CLERK_SIGN_IN_URL` | Sign-in route (`/login`). |
| `NEXT_PUBLIC_CLERK_SIGN_UP_URL` | Sign-up route (`/sign-up`). |
| `NEXT_PUBLIC_CLERK_JWT_TEMPLATE` | Clerk JWT template name — **`redarch-km`** — that injects `email` + `username` into the bearer token. |
| `NEXT_PUBLIC_API_URL` | API base the bearer token is sent to. |

## 11. Security notes

- **Secrets are never returned or logged after creation.** API keys, inbound signing
  secrets, and connection secrets are shown to the admin **once** at creation; storage is
  a SHA-256 hash (API keys, webhook tokens) or Fernet ciphertext (signing + connection
  secrets). Read endpoints expose metadata / `has_secret` only.
- **Opaque failures.** Auth failures return a single `401` with no detail about which
  check failed (unknown vs revoked vs expired vs bad signature), denying an oracle.
- **Constant-time comparison** guards the internal-API key and webhook HMAC checks.
- **`azp` is mandatory** for Clerk verification and is default-deny; a deployment cannot
  start Clerk verification without `CLERK_ALLOWED_AZP`.
- **Rotation.** API keys have an optional `expires_at` and can be revoked instantly
  (`DELETE /api/api-keys/{id}`); prefer short-lived keys for integrations. Signing and
  connection secrets rotate by editing the endpoint/connection (a new secret is generated /
  supplied and re-encrypted).
- **If a key leaks:** revoke it immediately (API key) or replace the endpoint/connection
  secret; because only hashes/ciphertext are stored, the leaked plaintext is the only copy,
  so revocation is the remedy. Rotate `ORG_ENCRYPTION_KEY` only with a re-encryption plan
  (it decrypts every per-org secret at rest). Never enable `API_E2E_TEST_MODE` in
  production, and keep all `pk_`/`sk_`/`API_E2E_TEST_SECRET` values in gitignored env files.
- **Deactivation is enforced at auth time** — a deactivated `user_profiles` row is rejected
  with `403` even with a valid Clerk token.

## 12. Cross-links

- [RBAC.md](RBAC.md) — the `access_mask` authorization model (orthogonal to the IdP).
- [DATABASE.md](DATABASE.md#row-level-security) — RLS policies, tenant context, roles.
- [API.md](API.md#enterprise-api--apiv1-org-api-key) — the `/api/v1` endpoint catalog.
- [CHANGE_MANAGEMENT.md](CHANGE_MANAGEMENT.md) — release promotion and the `config:read`/`config:write` receiver.
- [WORKFLOW_ENGINE.md](WORKFLOW_ENGINE.md) — inbound-triggered runs and connection actions.
- MCP_AND_INTEGRATIONS.md — inbound webhooks and external integration surface.
- [ARCHITECTURE.md](ARCHITECTURE.md) — services and Go migration status.
- `docs/design/clerk-auth-migration-spec.md` — historical Keycloak→Clerk migration plan
  (superseded by this doc).

## Known gaps / TODO

- **Go parity is not CI-gated.** The Go `services/api-go` verifier is intended to mirror
  `auth/clerk.py`, but the Python service is the authoritative, CI-covered implementation
  today (see ARCHITECTURE.md).

> Reviewed 2026-07-16 against Alembic migration 039. Source of truth is the code; if this doc disagrees with the code, the code wins.
