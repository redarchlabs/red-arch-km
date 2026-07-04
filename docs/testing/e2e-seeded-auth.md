# Running the seeded E2E suite locally (`E2E_WITH_BACKEND=1`)

This is the repeatable recipe for running the `ui/` Playwright auth + RBAC specs
against a **real seeded backend**. Without `E2E_WITH_BACKEND=1` the heavier specs
`test.skip()` and only `smoke.spec.ts` runs.

Tracker: RED-8 (T1) / feature RED-7. Feeds the full seeded run (T3, RED-10).

## How auth actually works in these specs (read this first)

The auth/RBAC specs do **not** log in through Clerk. They authenticate to the
Python API using a test-only header bypass:

- `X-Test-User: <username>:<email>` + `X-Test-Secret: <shared secret>`
- Enabled server-side by `API_E2E_TEST_MODE=true` + `API_E2E_TEST_SECRET=<secret>`
  (see `services/api/src/api/auth/dependencies.py`).
- Users named in the specs (`e2e_admin`, `regular_user`, `user_a`, `user_b`) are
  **auto-provisioned in Postgres** on first request — no Clerk users required.
- Org, permission dimensions (regions/departments/roles/groups), the `e2e_admin`
  site-admin, folders and tags are seeded by `services/api/scripts/seed_e2e.py`.

**Clerk is only needed for the UI to boot** and to satisfy `smoke.spec.ts`'s
"root redirects to /login" assertion. We use the Clerk **TEST** instance
`magnetic-albacore-4` (development keys, `pk_test_`/`sk_test_`). No Clerk-side
seeding is involved.

## One-time setup

1. **UI Clerk keys** — copy the template and fill in the real test-instance keys:

   ```bash
   cp ui/.env.local.example ui/.env.local
   # edit ui/.env.local: set NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY + CLERK_SECRET_KEY
   # to the "magnetic-albacore-4" values (Clerk dashboard -> API keys, or the
   # WO decision log). ui/.env.local is gitignored — never commit it.
   ```

   The auto-generated keyless instance under `ui/.clerk/.tmp/` is superseded by
   this test instance and can be ignored.

2. **Backend `.env`** — the repo `.env` (used by `make`/compose) must contain:

   ```
   API_E2E_TEST_MODE=true
   API_E2E_TEST_SECRET=<some shared secret>     # e.g. dev-test-secret
   ```

   (Already present in the standard dev `.env`.) The Clerk backend path is **not**
   required for this run — leave `CLERK_JWT_ISSUER` unset so the API stays on the
   test-header bypass.

## Run recipe

```bash
# 1. Infra + API (Postgres, Redis, Qdrant, Neo4j, api on :8000).
make dev-infra            # infra only ...
make migrate              # apply Alembic migrations
# ... then start the API (either `make dev` for the full stack, or run the api
#     container / `uv run uvicorn` — it must serve http://localhost:8000).

# 2. Seed the org, roles, and e2e_admin (idempotent).
make seed-e2e             # -> prints SEEDED_ORG_ID=<uuid>

# 3. Run the seeded Playwright suite. The E2E_TEST_SECRET value MUST equal the
#    server's API_E2E_TEST_SECRET. Playwright's webServer auto-starts `next dev`,
#    which reads ui/.env.local (the Clerk keys) automatically.
cd ui
E2E_WITH_BACKEND=1 \
E2E_TEST_SECRET="<same value as API_E2E_TEST_SECRET>" \
  npx playwright test
```

### Env vars the harness reads (defaults in parentheses)

| Var | Purpose | Default |
| --- | --- | --- |
| `E2E_WITH_BACKEND` | Un-skips the seeded specs; enables `global-setup.ts` | (unset) |
| `E2E_TEST_SECRET` | Must match server `API_E2E_TEST_SECRET` | (required) |
| `E2E_API_URL` | API base for `global-setup.ts` | `http://localhost:8000` |
| `E2E_UI_URL` | UI base recorded into `.state.json` | `http://localhost:3000` |
| `E2E_TEST_USER` | Admin identity for global setup | `e2e_admin:e2e_admin@e2e.local` |
| `BASE_URL` | If set, Playwright targets it and skips `next dev` | (unset -> starts dev) |

`global-setup.ts` connects to the API as `e2e_admin`, finds the **"E2E Test Org"**,
and writes `ui/tests/e2e/.state.json`. Its main job today is a **fail-fast seed
check** — it errors out early if the DB isn't seeded. (The captured `orgId` is
recorded via the `e2eState` fixture but the current auth/rbac specs do not yet
read it; org-scoping via an `X-Org-ID` header is a RED-9 follow-up.)
`.state.json` contains the shared test secret in plaintext and is **gitignored**
(`*.state.json`) — never commit it.

## Notes / known gaps

- **✅ RESOLVED IN T3 (commit `49dd674`) — the blocker below is historical.**
  T3 repointed every `request.*` call in `auth.spec.ts` and `rbac.spec.ts` from
  `baseURL` (`:3000`) to `e2eState.apiUrl` (`:8000`), eliminating the 404
  phantom-route tautology. The specs now genuinely exercise the Python API, so
  the T3 green result (auth 8/8, rbac 10/10) is a real correctness signal, not a
  vacuous one. The original note is kept below for history.
- **⚠ (HISTORICAL) KNOWN T3 BLOCKER — request-based specs target the UI origin, not the API.**
  `auth.spec.ts` and `rbac.spec.ts` make their `request.get/post(...)` calls
  against `baseURL` (`playwright.config.ts` -> `http://localhost:3000`, the UI),
  but `ui/next.config.ts` has **no `/api` rewrite** and there are no
  `app/api/*` route handlers — so `http://localhost:3000/api/...` is served by
  `next dev` and **404s** instead of reaching the Python API on `:8000`. Under
  this recipe those specs' positive assertions (e.g. admin `expect(res.ok())`)
  will fail, and the `[403,404]` deny-pairs pass for the *wrong* reason
  (route-absent). This is a **pre-existing spec defect** (T1 didn't touch the
  specs) of the same "phantom route" class as the deferred RED-9 item. Fix is a
  design call, tracked separately: either (a) add a test-env Next rewrite
  `/api/:path* -> http://localhost:8000/api/:path*`, or (b) point the
  request-based specs at the API origin (`E2E_API_URL` / `e2eState.apiUrl`)
  instead of `baseURL`. The browser/Page-Object specs (documents-full,
  folders-full, brain-api, chat-rag) correctly drive the UI on `:3000` and are
  unaffected.
- **Port collision:** do NOT also start the compose `ui` container (`make dev`
  brings it up on `:3000`) while running `npx playwright test` — Playwright's
  `webServer` starts its own `next dev` on `:3000` and the two collide. For the
  local recipe run infra + API only and let Playwright own `:3000` (or set
  `BASE_URL=http://localhost:3000` against an already-running UI to skip
  Playwright's `webServer`).
- **Runtime protocol:** the actual full seeded run (T3) executes in the MAIN
  worktree, not an isolated worktree (isolated worktrees can't fetch/seed
  reliably). Create `ui/.env.local` in whichever checkout runs the UI.
- **Containerized UI gap:** the `ui` service in `docker/docker-compose.yml` has no
  `env_file`/build-args passing the Clerk keys, and Next.js bakes
  `NEXT_PUBLIC_*` at **build** time. The local recipe above sidesteps this by
  running `next dev` (which reads `ui/.env.local`). Wiring the container image is
  out of scope for T1 — flagged for the compose/CI owner.
- **Secret hygiene:** keep all `pk_test_`/`sk_test_` and `API_E2E_TEST_SECRET`
  values in gitignored env files only; never echo them into logs, CI output, or
  Playwright traces/artifacts.
