# KM2 MCP Server

An [MCP](https://modelcontextprotocol.io) server that lets an agent (Claude Code, etc.)
drive the **Red Arch Knowledge Manager (KM2)** API on your behalf.

## How auth works (the important part)

You sign in **once, in a browser**, through the app's normal Clerk flow (SSO/MFA
and all). This server owns a **persistent Playwright Chromium profile** pointed at
the running KM2 web app, and for every API call it harvests a fresh token via
`window.Clerk.session.getToken()` — the exact path the app itself uses — plus the
active org from the same `localStorage` key the app writes.

- **No secrets are stored by this server.** It rides *your* live session with
  *your* permissions. RLS/org-scoping is still enforced server-side.
- Clerk.js owns token refresh/rotation, so there's no hand-rolled token logic.
- The session persists in the profile dir, so you only re-login when the Clerk
  session itself expires.

The only sensitive artifact on disk is the profile directory (session cookies).
It is created `0700` and is gitignored — **never commit it.**

## Prerequisites

- Node.js ≥ 20 (tested on 24).
- The KM2 stack running locally: web app on `http://localhost:3000`, API on
  `http://localhost:8000/api` (the defaults; see `run-stack.sh`).
- A Clerk account that is a **member of at least one org** (the "No organizations"
  state can't make org-scoped calls — that's surfaced as a clear error).

## Install & build

```bash
cd tools/km2-mcp
npm install
npx playwright install chromium   # one-time: the browser this server drives
npm run build                     # emits dist/
npm test                          # optional: unit tests (no browser/network)
```

## Register with Claude Code

A project-level [`.mcp.json`](../../.mcp.json) at the repo root already registers this
server as `km2`. Claude Code will prompt you to approve it. To register manually
(or at user scope), add:

```json
{
  "mcpServers": {
    "km2": {
      "command": "node",
      "args": ["tools/km2-mcp/dist/index.js"]
    }
  }
}
```

Then in a session: run `km2_login`, complete the browser sign-in, and use any tool.

## Configuration (env)

All optional — the defaults target a local dev stack. **No secrets here.**

| Env var | Default | Purpose |
|---|---|---|
| `KM2_APP_URL` | `http://localhost:3000` | KM2 web app (the Clerk origin tokens are harvested from) |
| `KM2_API_URL` | `http://localhost:8000/api` | KM2 backend base URL (includes `/api`) |
| `KM2_CLERK_JWT_TEMPLATE` | *(unset)* | Mirror `NEXT_PUBLIC_CLERK_JWT_TEMPLATE` if the app sets one |
| `KM2_ORG_ID` | *(unset)* | Hard override for `X-Org-ID` (wins over the app's active org) |
| `KM2_ORG_STORAGE_KEY` | `redarch:currentOrgId` | localStorage key the app stores the active org under |
| `KM2_USER_DATA_DIR` | `~/.km2-mcp/profile` | Persistent browser profile (holds the Clerk session) |
| `KM2_BROWSER_CHANNEL` | `chrome` (in repo `.mcp.json`) | Browser to drive (`chrome`/`msedge`); real Chrome avoids IdP bot-detection. Unset = bundled Chromium |
| `KM2_HEADLESS` | `false` | Run headless. Keep `false` for interactive login |
| `KM2_LOGIN_TIMEOUT_MS` | `180000` | How long `km2_login` waits for you to finish signing in |
| `KM2_LOG_LEVEL` | `info` | `debug`/`info`/`warn`/`error` (to **stderr** only) |

## Usage flow

1. `km2_login` — opens the browser; complete sign-in. Returns immediately if a
   session is already persisted.
2. `km2_status` — confirms who you are and which org is active.
3. `km2_list_orgs` / `km2_set_org` — if no org is active or you want a different one.
4. Any resource tool, e.g. `km2_create_connection`, `km2_list_workflows`,
   `km2_search`.

## Tool catalog (69 tools)

- **Session:** `km2_login`, `km2_status`, `km2_list_orgs`, `km2_set_org`
- **Connections:** list / create / update / delete
- **Workflows:** list / get / create / update / delete, versions (list / save /
  publish / test), `km2_run_workflow`, runs (`km2_recent_runs`,
  `km2_list_workflow_runs`, `km2_get_run_steps`), `km2_complete_task`, inbound
  endpoints (list / create / delete)
- **Documents:** list / get / create / update / delete, content (get / update),
  reprocess
- **Folders:** list / get / create / update / delete
- **Forms:** list / get / create / update / delete, links (list / create /
  revoke), render, submit
- **Views:** list / get / create / update / delete, render
- **Entities:** definitions (list / get / create / update / delete), fields
  (add / delete), relationships (list / create); records (list / get / create /
  update / delete)
- **Search:** `km2_search`

## Not covered (by design)

- **Binary/file upload** (`POST /documents/upload`, multipart) — use the web UI.
- **SSE streams** (chat/agent/run streams) — not meaningful as one-shot tools.
- **Site-admin / cross-org** operations — this server acts as your user in one org.

## Security notes

- Acts strictly as the signed-in user; **no privilege elevation** and no E2E/test
  bypass. All authorization is enforced by the backend.
- Tokens are harvested per-call, short-lived, and never logged.
- The profile dir holds session cookies — treat it like a credential; it's
  gitignored and created `0700`.
- Point `KM2_APP_URL`/`KM2_API_URL` only at trusted origins.

## Troubleshooting

- **"Not signed in…"** → run `km2_login` and finish the browser sign-in.
- **"No active organization…"** → your account has no org selected; use
  `km2_list_orgs` + `km2_set_org`, or set `KM2_ORG_ID`.
- **`km2_login` times out** → finish signing in, then call it again (it detects the
  session immediately). Keep `KM2_HEADLESS=false` so you can interact.
- **403 on a call** → the active org is wrong, or the action needs org-admin.
- **Browser won't launch** → run `npx playwright install chromium`.
- **"Your browser is a bit unusual" / SSO blocks sign-in** → the IdP is
  bot-detecting the automation browser. This server already drives real Chrome
  (`KM2_BROWSER_CHANNEL=chrome`) with the automation fingerprint stripped. If it
  still trips, ensure Chrome is installed, and delete `~/.km2-mcp/profile` to
  start from a clean profile, then `km2_login` again.
