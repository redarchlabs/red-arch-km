# Seeded E2E suite — CI strategy & decision (RED-11 / T4)

Tracker: RED-11 (T4) / WO-2026-07-04-e2e-seeded-auth-coverage. Basis: T3 (RED-10)
verified the in-scope seeded suite green at tip `99b1d27` — `auth` 8/8, `rbac`
10/10, `smoke` 2/2, skip=0. Companion recipe: [`e2e-seeded-auth.md`](./e2e-seeded-auth.md).

## DECISION

The seeded `E2E_WITH_BACKEND=1` auth/RBAC E2E suite runs **on-demand (manual) and
nightly (scheduled) only — NOT per-PR.**

- **On-demand:** `workflow_dispatch` on the `E2E Seeded (auth/RBAC)` workflow.
- **Nightly:** `schedule` (cron), one run per day.
- **NOT** triggered by `pull_request` or `push`.

The lightweight `smoke`-only E2E job (with `E2E_WITH_BACKEND` **unset**, so the
heavy specs `test.skip()`) stays on every PR — that is the existing `test-e2e`
job in [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) and is
unchanged by this decision. Only the **seeded** run is gated to manual + nightly.

### Rationale

1. **Secret exposure surface.** The seeded run needs the Clerk **test-instance**
   keys (`pk_test_`/`sk_test_`) and the shared `API_E2E_TEST_SECRET`. Running it
   per-PR would expose these secrets to PR-triggered workflow contexts (including,
   depending on repo settings, forked-PR contexts). Keeping it on
   `workflow_dispatch` + `schedule` — both of which run in the **base-repo**
   trusted context — means PR authors never gain access to the secrets.
2. **Cost & flake surface.** The seeded run stands up the full backend
   (Postgres, Redis, and the Python API on `:8000`), seeds the DB, and drives a
   real browser. That is materially slower and more flake-prone than the unit /
   type-check / smoke gates. Paying that cost on **every** PR is not justified
   until the suite has proven stable over a run of nightly executions.
3. **Reversible.** This is deliberately conservative. Once the nightly run is
   demonstrably stable, revisit promoting it to a per-PR (or label-gated /
   merge-queue) gate. See "Revisit criteria" below.

## HOW TO RUN

The exact seeded run recipe (infra up → migrate → `seed-e2e` →
`E2E_WITH_BACKEND=1` playwright invocation, and the gitignored `ui/.env.local`
Clerk keys) is documented **once** in
[`e2e-seeded-auth.md`](./e2e-seeded-auth.md). Do not duplicate it here — that doc
is the single source of truth for the local recipe.

The CI job in [`.github/workflows/e2e-seeded.yml`](../../.github/workflows/e2e-seeded.yml)
automates the same recipe:

1. Bring up infra + API (`docker compose … up -d api`, which pulls in Postgres +
   Redis via `depends_on` and serves the API on `:8000`).
2. `make migrate` (Alembic `upgrade head`).
3. `make seed-e2e` (idempotent org/roles/`e2e_admin` seed).
4. Write `ui/.env.local` from CI **secrets** (never committed — see below).
5. `bunx playwright install --with-deps` then run the scoped specs with
   `E2E_WITH_BACKEND=1` and `E2E_TEST_SECRET` == the server's `API_E2E_TEST_SECRET`.
   Playwright's `webServer` starts its own `next dev` on `:3000` (reads
   `ui/.env.local`); the compose `ui` container is **not** started (port `:3000`
   collision — see the recipe doc's "Port collision" note).

## SCOPE of the CI job

**In-scope (currently green, the job runs exactly these):**

| Spec | T3 result |
| --- | --- |
| `smoke.spec.ts` | 2/2 |
| `auth.spec.ts` | 8/8 |
| `rbac.spec.ts` | 10/10 |

**Explicitly NOT yet CI-ready (the job does NOT run these — do not add them until
the infra follow-up lands):**

`brain-api.spec.ts`, `chat-rag.spec.ts`, `documents.spec.ts`,
`documents-full.spec.ts`, `folders-full.spec.ts`, `journey.spec.ts`.

These 6 specs need two things the current job does not provide:

1. **The brain service on `:8020`** (RAG / documents / chat paths) — not started
   by this workflow.
2. **Browser-level Clerk sign-in.** The in-scope specs authenticate to the Python
   API via the `X-Test-User` / `X-Test-Secret` **header bypass** (API-only). The
   6 out-of-scope specs drive the UI through a real logged-in browser session,
   which needs actual Clerk auth in the browser — not the header bypass.

Scoping the CI job to the 3 green specs is deliberate: it ships a **green**
pipeline rather than a red one. Bringing the other 6 online is a **follow-up
(T6 / infra)** — tracked separately; do not widen this job's spec list until that
work lands and verifies those specs green under CI.

## SECRET MANAGEMENT

- All Clerk test-instance keys and the shared test secret come from **CI repo
  secrets** — never committed. The workflow references the following
  **placeholder** secret names (the human wires the real values in repo settings
  → Secrets and variables → Actions):

  | Secret name (placeholder) | Maps to | Notes |
  | --- | --- | --- |
  | `E2E_CLERK_PUBLISHABLE_KEY` | `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` | `pk_test_…` (Clerk **test** instance `magnetic-albacore-4`) |
  | `E2E_CLERK_SECRET_KEY` | `CLERK_SECRET_KEY` | `sk_test_…` |
  | `E2E_TEST_SECRET` | `E2E_TEST_SECRET` / server `API_E2E_TEST_SECRET` | shared header-bypass secret; must match server |

- Per-PR triggering is **explicitly avoided** so these secrets are never exposed
  to PR-context (or forked-PR) workflow runs. `workflow_dispatch` and `schedule`
  both run in the trusted base-repo context.
- Never echo secret values into logs, CI output, or Playwright traces/artifacts
  (see the recipe doc's "Secret hygiene" note). `ui/.env.local` and `*.state.json`
  are gitignored.

## Revisit criteria (when to promote to per-PR)

Promote the seeded run to a per-PR / label-gated / merge-queue gate once **all**
of the following hold:

1. The nightly run has been **green and non-flaky** across a meaningful streak
   (e.g. ≥ 2 weeks of nightly runs) with no intermittent failures.
2. A secrets strategy safe for PR context is in place (e.g. required-reviewer
   environments, or forked-PR runs explicitly excluded from secrets).
3. Runtime is within an acceptable PR-latency budget.

Until then, keep it manual + nightly.
