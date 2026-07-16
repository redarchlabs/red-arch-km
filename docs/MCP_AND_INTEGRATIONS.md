# MCP & Integrations

How KM2 talks to the outside world: the `km2-mcp` developer tool that drives the
API, the "Connect" OAuth flow that lets agents reach external MCP servers, inbound
webhooks that start workflow runs, outbound connections that call third-party HTTP
APIs, and the config-push transport used for cross-instance promotion. For
engineers integrating with KM2 or extending its automation surface.

## Table of Contents

- [Overview](#overview)
- [The km2-mcp developer tool](#the-km2-mcp-developer-tool)
- [Agent MCP connections (the "Connect" OAuth flow)](#agent-mcp-connections-the-connect-oauth-flow)
- [Inbound webhooks](#inbound-webhooks)
- [Outbound connections](#outbound-connections)
- [Config-push transport](#config-push-transport)
- [Cross-links](#cross-links)

## Overview

KM2 exposes several distinct integration surfaces. They differ in direction (who
calls whom), how they authenticate, and what they are for. Do not conflate them —
the two "MCP" surfaces in particular are unrelated: one is a local dev tool that
drives KM2, the other is a server-side feature that lets KM2's agents call *out* to
external MCP servers.

| Surface | Direction | Auth | Primary use |
|---|---|---|---|
| `km2-mcp` dev tool | Dev machine → KM2 API | Live Clerk browser session (harvested per call) | Drive KM2 from Claude Code / automation during development |
| Agent MCP connections ("Connect") | KM2 agents → external MCP server | OAuth 2.1 (PKCE), org- or user-scoped, tokens Fernet-encrypted | Give autonomous agents external tools |
| Inbound webhooks | External sender → KM2 | Opaque URL token + optional HMAC `X-KM2-Signature` | Start a workflow run from an external event (real-time) |
| Outbound connections | KM2 (workflow / form) → external HTTP API | Saved connector credential (`bearer` / `api_key` / `basic`) | Call third-party APIs and robots from workflows/forms |
| Config-push transport | KM2 instance → another KM2 instance | Remote org API key (`Bearer`), SSRF-guarded, HTTPS | Promote a config release to another deployment |

## The km2-mcp developer tool

`tools/km2-mcp` is a stdio [Model Context Protocol](https://modelcontextprotocol.io)
server that lets an MCP client (Claude Code, etc.) drive the KM2 REST API on behalf
of a signed-in user. It is a **development/automation tool**, not part of the
deployed product. Entry point: `tools/km2-mcp/dist/index.js` (`src/index.ts`).

### Auth: the Clerk session bridge

The server owns no secrets and mints no tokens of its own. Instead it rides *your*
live browser session (`src/browserSession.ts`, class `BrowserSession`):

- It launches a **persistent Playwright Chromium profile** (`KM2_USER_DATA_DIR`,
  default `~/.km2-mcp/profile`, created `0700`) pointed at the running web app.
- You sign in once through the app's normal Clerk flow (SSO/MFA and all); the
  session persists in the profile.
- For every API call it harvests a fresh JWT via `window.Clerk.session.getToken()`
  — the exact call path the app uses — and reads the active org from the same
  `localStorage` key the app writes (`redarch:currentOrgId`, overridable via
  `KM2_ORG_STORAGE_KEY`).
- Clerk.js owns token refresh/rotation, so there is no hand-rolled token logic. The
  only sensitive artifact on disk is the gitignored profile dir.

Because it acts strictly as the signed-in user, there is no privilege elevation and
no E2E/test bypass: all RLS/org-scoping and authorization are still enforced
server-side by the API. The launch strips the automation fingerprint
(`--disable-blink-features=AutomationControlled`, `navigator.webdriver` masked) so
identity-provider bot detection does not reject SSO sign-in.

### Tool categories

The tool catalog (README: 69 tools) is organized by resource, one source module per
category under `tools/km2-mcp/src/tools/`:

| Category | Source | Examples |
|---|---|---|
| Session | `session.ts` | `km2_login`, `km2_status`, `km2_list_orgs`, `km2_set_org` |
| Connections | `connections.ts` | list / create / update / delete |
| Workflows | `workflows.ts` | CRUD, versions, `km2_run_workflow`, runs, inbound endpoints |
| Documents | `documents.ts` | CRUD, content get/update, reprocess |
| Folders | `folders.ts` | CRUD |
| Forms | `forms.ts` | CRUD, links, render, submit |
| Views | `views.ts` | CRUD, render |
| Entities | `entities.ts` | definitions, fields, relationships, records CRUD |
| Reports | `reports.ts` | create / run / aggregate |
| Search | `search.ts` | `km2_search` (knowledge/RAG search) |

Binary/file upload, SSE streams (chat/agent/run streams), and site-admin/cross-org
operations are intentionally **not** exposed — use the web UI for those.

### Registration

The repo-root [`.mcp.json`](../.mcp.json) registers this server as `km2` at project
scope, so Claude Code prompts to approve it. It points at a local dev stack
(`KM2_APP_URL=http://localhost:3000`, `KM2_API_URL=http://localhost:8000/api`,
`KM2_BROWSER_CHANNEL=chrome`). All config is via env vars and holds no secrets; see
`src/config.ts` (`loadConfig`) and the tool's own `README.md` for the full table.
Typical flow: `km2_login` → `km2_status` → any resource tool.

## Agent MCP connections (the "Connect" OAuth flow)

Separately from the dev tool, KM2's autonomous agents can reach **external** MCP
servers as tool providers. Servers are registered per org and connected through an
OAuth 2.1 browser flow; a connected server's id is added to an agent's
`mcp_server_ids`. This is owned by [AGENT_ORG.md §7](AGENT_ORG.md); summarized here
because it is a KM2 integration surface.

### Schema (migration 032)

`032_mcp_oauth.py` extends `mcp_servers` and adds two supporting tables:

| Object | Purpose |
|---|---|
| `mcp_servers.oauth_identity` | `org` or `user` — whose credentials the connection uses |
| `mcp_servers.oauth_client_secret_encrypted` | client secret (Fernet-encrypted) |
| `mcp_servers.oauth_{access,refresh}_token_encrypted`, `oauth_token_expires_at` | org-mode tokens |
| `mcp_server_user_tokens` | per-user access/refresh tokens (user-mode), unique per `(mcp_server_id, user_profile_id)` |
| `mcp_oauth_flows` | in-flight authorizations: `state`, PKCE `code_verifier`, `redirect_uri` |

All secrets and tokens are Fernet-encrypted; `mcp_server_user_tokens` and
`mcp_oauth_flows` are org-scoped under Row-Level Security.

### Endpoints (`routers/mcp_servers.py`, mounted at `/api/agents`)

| Method + path | Auth | Purpose |
|---|---|---|
| `GET /api/agents/mcp-servers/presets` | org-admin | Known server presets |
| `GET /api/agents/mcp-servers` · `POST` · `PATCH /{id}` · `DELETE /{id}` | org-admin | Server CRUD |
| `POST /api/agents/mcp-servers/{id}/oauth/start` | org-admin | Begin the OAuth flow; returns `authorization_url` |
| `GET /api/agents/mcp-servers/oauth/callback` | **public** | Provider redirect target; the random `state` is the capability |
| `POST /api/agents/mcp-servers/{id}/oauth/disconnect` | org-admin | Revoke stored tokens |
| `POST /api/agents/mcp-servers/{id}/test` | org-admin | Connect and list the server's tools |

`oauth_start`/`complete_authorization` are implemented in the agents MCP service
(`services/agents/mcp/`, class `McpOAuthService`). The callback is unauthenticated
by design — the server-side `state` row (with its `code_verifier`) is the only
capability, so nothing else is needed to complete the exchange. See
[AGENT_ORG.md](AGENT_ORG.md) for how connected tools are granted to agents
(`mcp__<server>__*` wildcards) and gated by `readOnlyHint` / server `read_only`.

## Inbound webhooks

An inbound endpoint is a public URL that starts a workflow run from an external
POST. It carries an opaque token in the path; when the endpoint has a signing
secret, the request must also carry a valid HMAC signature — so a leaked URL alone
cannot trigger the workflow.

### Endpoint records (migrations 020, 022)

`020_workflow_inbound_endpoints.py` creates `workflow_inbound_endpoints`:

| Column | Notes |
|---|---|
| `token_hash` | SHA-256 hex of the URL token — only the hash is stored (unique) |
| `workflow_id` | workflow to run |
| `enabled` | disabled endpoints return 404 |
| `signing_secret_encrypted` | (migration `022`) Fernet-encrypted HMAC secret; `NULL` = legacy token-only |
| `org_id` | RLS tenant key |

The table is org-scoped under RLS. The token secret is never recoverable (only its
hash is stored); the signing secret is stored encrypted because the receiver must
recover it to recompute the signature.

### Managing endpoints (`routers/workflows.py`, `/api/workflows`)

| Method + path | Auth | Purpose |
|---|---|---|
| `GET /api/workflows/inbound-endpoints` | org-admin | List (returns `has_signing_secret`, never the secret) |
| `POST /api/workflows/inbound-endpoints` | org-admin | Create; returns `url`, `token`, `signing_secret`, `signature_header` **once** |
| `DELETE /api/workflows/inbound-endpoints/{id}` | org-admin | Delete |

On create, the server generates a URL token (`secrets.token_urlsafe(32)`) and a
signing secret (`whsec_` + `token_urlsafe(32)`), stores only the token's hash and
the encrypted secret, and returns the full URL
`{public_base_url}/api/inbound/{token}` plus the plaintext secret — shown once.

### HMAC signing

The signature scheme (`services/workflow/webhook_signing.py`) mirrors Stripe/GitHub.
The header is:

```
X-KM2-Signature: t=<unix_seconds>,v1=<hex hmac-sha256>
```

The signed message is `f"{t}.{raw_body}"` over the **exact** raw request bytes
(re-serialized JSON would not round-trip). The receiver recomputes the HMAC with the
stored secret, compares constant-time, and rejects timestamps outside a
±`DEFAULT_TOLERANCE_SECONDS` (300s) window to bound replay. Full details of the
scheme are in [AUTHENTICATION.md](AUTHENTICATION.md).

### Receiving and execution

`POST /api/inbound/{token}` (`routers/inbound.py` → `services/workflow/inbound.py`,
`trigger_from_inbound`):

1. Reads the exact request bytes (HMAC is computed over them; JSON input is parsed
   from them).
2. Resolves the endpoint by token hash on a **privileged, RLS-bypassing session** —
   the lookup spans every org because the token alone selects the tenant.
3. Verifies the signature if the endpoint requires one. Every failure mode
   (missing / malformed / stale / mismatch) maps to a single opaque `401` so a
   caller gets no oracle.
4. **Downgrades** to the endpoint's org (`SET LOCAL ROLE app_user` + tenant GUC)
   before any write, so the run and everything it does are RLS-enforced.
5. Creates the run with `trigger_operation="webhook"` and drives it **inline** to
   quiescence (`engine.start_run` + `engine.drive_run`) — the workflow executes
   immediately rather than waiting for the next beat sweep. A run that parks
   (timer/receive) returns at the park; the beat sweep is the backstop that resumes
   it. Returns `{"run_id", "status"}`.

The JSON body becomes the run's trigger input and is available to workflow templates
as `after.*` (e.g. a robot's "heard" webhook lets a step read `{{ after.text }}`).
See [WORKFLOW_ENGINE.md](WORKFLOW_ENGINE.md) for how a run consumes trigger input.

## Outbound connections

A connection is a reusable, org-scoped connector credential for calling an external
HTTP API. Workflows and forms reference a connection by name; the secret is injected
server-side and never exposed to the browser.

### Connection records (migration 019)

`019_workflow_connections.py` creates `workflow_connections` (org-scoped, RLS):

| Column | Notes |
|---|---|
| `name` | unique per org (`uq_workflow_connection_name_per_org`) |
| `kind` | connector kind, default `http` |
| `base_url` | target base URL |
| `auth_type` | `none` \| `bearer` \| `api_key` \| `basic` |
| `secret_encrypted` | Fernet-encrypted credential (write-only; API returns only `has_secret`) |
| `config` | JSONB (e.g. `header` name for `api_key`, `username` for `basic`) |

Auth headers are built by `_auth_headers` in `services/workflow/actions.py`:

| `auth_type` | Header sent |
|---|---|
| `bearer` | `Authorization: Bearer <secret>` |
| `api_key` | `<config.header or X-API-Key>: <secret>` |
| `basic` | `Authorization: Basic base64(<config.username>:<secret>)` |
| `none` | none |

### Managing connections (`/api/workflows/connections`, org-admin)

`GET` / `POST` / `PATCH /{id}` / `DELETE /{id}` in `routers/workflows.py`. `secret`
is write-only and never returned; `PATCH` re-encrypts only when a new secret is
supplied.

### The call endpoint

`POST /api/workflows/connections/call` (`ConnectionCall` → `ConnectionCallResult`)
is callable by **any org member** (`require_org_access`), because it backs a form's
`call_connection` button. It resolves the named connection, injects its auth, and
performs the request — bounded exactly like a workflow `http_request`: the target
host must pass the deny-by-default SSRF allow-list (`WORKFLOW_WEBHOOK_ALLOWLIST` /
`WORKFLOW_TRUSTED_LOCAL_HOSTS`), and private IPs are blocked. The response comes
back as `{ok, status_code, body}`; the secret is never echoed.

### Workflow `http_request` and templated bodies

Inside a workflow, the `http_request` action (`services/workflow/actions.py`,
`HttpRequest`) resolves `config.connection`, injects auth, and calls
`base_url + config.path` (or a literal `config.url`) under the same SSRF guard. The
JSON **body** is templated: `{{ after.x }}` / `{{ vars.x }}` tokens are rendered
(`_render_deep`) so a request can carry values from the trigger or an earlier
captured step. The URL/host is intentionally **not** templated — it stays pinned to
the connection and the allow-list.

**Robot / `call_connection` example.** A robot is registered as a connection
(`auth_type=api_key`, `config.header=X-KM2-Command-Key`). A form's `call_connection`
button, or a workflow `http_request`, posts a command (e.g. speak/gesture) to the
robot's endpoint; the command secret is injected server-side. This is how a "heard →
knowledge_search → say" loop reaches the physical robot in real time.

## Config-push transport

For cross-instance config promotion, `services/api/src/api/services/migration/transport.py`
(`OutboundPushClient`) pushes a frozen release bundle to **another KM2 deployment's**
inbound config receiver `POST /api/v1/config/promotions`, authenticated with that
instance's org API key (`Authorization: Bearer <km2_…>`). Every push:

- passes the same deny-by-default SSRF allow-list the workflow webhook actions use
  (`assert_outbound_host_allowed`);
- requires **HTTPS** unless the host is an explicitly trusted local bridge;
- is size-capped at `MAX_BUNDLE_BYTES` (both request and response).

`ping()` probes reachability and the key's config access via
`GET /api/v1/config/ping`. Local-org promotion does not use this transport — it runs
the executor in-process. This is only the remote leg; the full release/promotion
model is documented in [CHANGE_MANAGEMENT.md](CHANGE_MANAGEMENT.md).

## Cross-links

- [AGENT_ORG.md](AGENT_ORG.md) — agents, and how connected MCP tools are granted/gated.
- [WORKFLOW_ENGINE.md](WORKFLOW_ENGINE.md) — how inbound triggers and outbound actions run inside a workflow.
- [AUTHENTICATION.md](AUTHENTICATION.md) — Clerk JWTs, org API keys, and the HMAC webhook signature scheme.
- [CHANGE_MANAGEMENT.md](CHANGE_MANAGEMENT.md) — release bundles and cross-instance promotion.
- [API.md](API.md) — the REST surface these tools drive.
- [README](../README.md) — repo overview and stack.

> Reviewed 2026-07-16 against Alembic migration 039. Source of truth is the code; if this doc disagrees with the code, the code wins.
