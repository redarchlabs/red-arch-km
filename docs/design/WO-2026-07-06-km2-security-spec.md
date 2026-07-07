# Design Spec — WO-2026-07-06-km2-security (RED-15 + RED-18)

**Author:** Solution Architect · **Date:** 2026-07-06 · **Repo:** `red-arch-km-2` · **Service:** `services/api-go`
**Status:** Design for review — **REV 2** (post design-review). RED-15 code has already landed (commit `2288221`); this spec documents root cause, verifies the landed fix against acceptance criteria, and flags residuals. RED-18 is net-new and unimplemented.

### Revision 2 — resolutions folded in

This revision addresses the principal-engineer's design-review verdict (**PASS with one blocking implementation gap**) and the TPM's §4 decisions.

- **BLOCKING GAP RESOLVED (PE review):** the landed RED-15 fix does `SET LOCAL ROLE app_user` on *every* `WithTenant` request, but `app_user` is granted **only `CONNECT`** — there are **zero table / schema / sequence `GRANT`s anywhere in the repo** (verified in the current tree: `docker/init-db.sql:24` grants `CONNECT` only; no `GRANT SELECT/INSERT/UPDATE/DELETE`, no schema `USAGE`, no sequence `USAGE` in any migration). Because `SET ROLE` does **not** inherit the base login role's privileges, on any fresh/target env where grants were not applied out-of-band **every `WithTenant` handler returns `permission denied`** — the "already-landed" fix is a hard outage, not merely "verify in target env." **Resolution:** promoted from a §1.3 footnote to a **hard T2 deliverable** — an idempotent GRANT migration is now required and specified (§1.7, AC-15.7, test T9). The prior integration test masked this (it asserted only `current_user == app_user`, not that a tenant read returns rows under `app_user`); AC-15.2/15.3 are strengthened to assert actual rows under `app_user` (§1.6).
- **§4 TPM decisions folded as DECIDED (no longer open):** (1) break-glass `KM_ALLOW_PRIVILEGED_DB_ROLE` — **APPROVED**, include, default hard-fail, WARN on use; (2) `SET LOCAL ROLE app_user` in `internal.go` — **APPROVED, in scope for this WO's build** (folded into T2, not a separate follow-up); (3) same assertion for `worker-go`/`brain-api-go` — **DEFERRED** to a follow-up WO (out of scope here). See §4.
- **PE build-facing notes** folded into §1.3, §2.2, §2.5, §3, §5.

**REV 2 committee findings folded in (all PASS / PASS-conditioned):**
- **requirements-auditor — PASS.** Carry-forward **C2**: `WithTenant` needs the base role to be able to *assume* `app_user` (membership), which is strictly more than RED-18's non-super/non-BYPASSRLS check → new §2.2 constraint, **AC-18.6**, and a `docs/DEPLOYMENT.md` line (§5). AC-18.1/18.2 wording softened to match the §2.5 func-level test.
- **security-analyst — PASS (conditioned MEDIUM)** and **devils-advocate — PASS (HIGH caveat)** both flagged the **§1.7 GRANT scope**: a blanket `GRANT … ON ALL TABLES` hands `app_user` unfiltered cross-tenant DML on the **7 tables with no RLS policy** (`orgs`, `user_profiles`, + junction tables `membership_*`, `document_tags`), and the junctions are written *directly under `WithTenant`* → cross-tenant IDOR write vector. **Resolved:** new **§1.8** (scoped grant + junction RLS backstop + coverage invariant), **AC-15.8**, tests **T10/T11/T12**, §1.7 reassurance corrected to "FORCE-RLS tables only," and the junction remedy raised as **§4.4 decision** (SA rec: add RLS). Also folded: external-I/O-inside-tx audit + timeout tuning (§1.3), pool-starvation note (§1.3), break-glass hardening — strict `=1`, env-only, per-startup WARN, ongoing degraded-health signal (§2.2).

---

## 0. Summary & why these two ship together

Both issues are facets of the **same tenant-isolation guarantee**: every query the API runs against a tenant table must be constrained by Postgres Row-Level Security (RLS) to the caller's org.

- **RED-15** — the tenant GUC `app.current_tenant_id` was being set on a *bare autocommit connection*, so it reverted the instant the `SET` statement returned, leaving later queries un-scoped.
- **RED-18** — the runtime DB role is never asserted at startup, so a misconfigured `DATABASE_URL` pointing at a superuser or `BYPASSRLS` role silently disables RLS for any code path that does not itself drop to `app_user`.

They interact multiplicatively. RED-15 on its own, **when the connection role is RLS-subject**, is *fail-closed* (an empty GUC matches no rows — queries return nothing, a loud functional break). RED-15 becomes *fail-open* — silent cross-tenant data exposure — **only when combined with a privileged runtime role**, which is exactly the gap RED-18 closes. Shipping RED-18 turns the entire class of "someone forgot to scope the connection" bugs from fail-open into fail-closed. That is why they are specified and delivered as one unit.

### Verified code seams (file:line)

| Concern | Location |
|---|---|
| RLS policy predicate | `migrations/003_harden_rls_nullif.up.sql:20` — `org_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid` |
| `FORCE ROW LEVEL SECURITY` on tenant tables | `migrations/001_initial_schema.up.sql:225-246` |
| Fixed `WithTenant` | `services/api-go/internal/db/pool.go:163-196` |
| Pre-fix `WithTenant` (root cause) | commit `80d5e3d:services/api-go/internal/db/pool.go` |
| Runtime role config | `services/api-go/internal/config/config.go:19` (`DatabaseURL`) |
| Pool creation (startup) | `services/api-go/cmd/server/main.go:63-71` |
| Role definitions | `docker/init-db.sql` — `app_user` (LOGIN, RLS-subject); `app_admin` (LOGIN, **BYPASSRLS**) |
| **`app_user` privilege gap (blocking)** | `docker/init-db.sql:24` — `app_user` granted **`CONNECT` only**; no table/schema/sequence `GRANT`s exist in any migration → `permission denied` under `SET ROLE app_user` (see §1.7) |
| **Non-`WithTenant` GUC path** | `services/api-go/internal/handlers/internal.go:105-117` — sets GUC in a tx but **never `SET ROLE app_user`** |

---

## 1. RED-15 — `pool.WithTenant` + `is_local` autocommit reverts the tenant GUC

### 1.1 Root cause

The pre-fix implementation (`80d5e3d`) was:

```go
func (p *Pool) WithTenant(ctx context.Context, orgID uuid.UUID) (*TenantConn, error) {
    conn, err := p.Acquire(ctx)
    // ...
    _, err = conn.Exec(ctx,
        "SELECT set_config('app.current_tenant_id', $1, true)", // is_local = true
        orgID.String(),
    )
    return &TenantConn{conn: conn, orgID: orgID}, nil // subsequent queries run on bare conn
}
```

Two independent facts combine to revert the GUC:

1. **`set_config(name, value, is_local := true)` has `SET LOCAL` semantics** — the setting is scoped to the *current transaction* and is discarded at transaction end (`COMMIT`/`ROLLBACK`).
2. **pgx runs every statement outside an explicit `Begin` in autocommit mode** — each `conn.Exec` / `conn.Query` executes inside its *own implicit transaction* that commits the moment the statement completes.

So `conn.Exec("SELECT set_config(..., true)")` ran in implicit transaction *T₀*, which committed and ended immediately — discarding the GUC. The next `TenantConn.QueryRow(...)` ran in a *different* implicit transaction *T₁*, where `current_setting('app.current_tenant_id', true)` was back to its empty default `''`.

**Resulting behavior, given the RLS predicate `org_id = nullif(current_setting(...), '')::uuid`:**

- With an empty GUC, `nullif('', '')` → `NULL`, so the predicate becomes `org_id = NULL` → `NULL` (never true).
- **If the connection role is RLS-subject (e.g. `app_user`):** every `SELECT` returns *zero rows*; every `INSERT` fails its `WITH CHECK`. This is *fail-closed* — a loud, data-losing functional break, not a silent leak.
- **If the connection role is superuser or `BYPASSRLS`:** RLS is skipped entirely, so the empty GUC is irrelevant and **all rows of all tenants are visible/writable** — *fail-open cross-tenant exposure*. (This is the RED-18 gap; see §2.)

The pre-fix code *also* never issued `SET ROLE app_user`, so in any deployment where the pool's login role was privileged, RED-15 was directly fail-open.

### 1.2 Fix options considered

| # | Option | Chosen? | Trade-off / why-not |
|---|---|---|---|
| A | Change `is_local=true` → `is_local=false` (session-level `SET`) on the bare conn | ✗ | GUC would persist on the *pooled* connection after `Release()` and leak into the next borrower's request → cross-tenant contamination. Strictly worse. |
| B | **Wrap the whole request in one explicit transaction; run `SET LOCAL ROLE app_user` + `set_config(...,true)` inside it; commit on `Release()`** | ✓ | Both settings are transaction-scoped and auto-reset on commit/rollback, so the pooled connection is always returned clean. Adds transaction semantics to every request (see regressions). This is the landed fix. |
| C | Keep autocommit; re-issue `set_config` before *every* statement | ✗ | Fragile (every call site must remember), racy across pooled conns, no atomicity, doubles round-trips. |
| D | `pgxpool` `AfterConnect`/`BeforeAcquire` hook to set the GUC per-acquire | ✗ | Still autocommit unless `is_local=false`, which reintroduces option A's leak; and the tenant isn't known at acquire time. |
| E | Session-scoped `SET` + explicit `RESET` in `Release()` | ✗ | Correct only if `Release()` always runs and never panics mid-request; a leaked connection carries a live tenant GUC. Transaction auto-reset (B) is strictly safer. |

### 1.3 Recommended fix (landed) with threat model

The landed implementation (`pool.go:163-196`) opens one transaction per `WithTenant` call and, inside it:

```go
tx, _ := conn.Begin(ctx)
tx.Exec(ctx, "SET LOCAL ROLE app_user")                                   // privilege drop
tx.Exec(ctx, "SELECT set_config('app.current_tenant_id', $1, true)", org) // tenant scope
// TenantConn.Query/QueryRow/Exec all run on tx; Release() commits (or rolls back).
```

**`SET LOCAL ROLE app_user` is load-bearing and complementary to the GUC.** RLS is evaluated against `current_user`'s attributes; dropping to `app_user` (which lacks superuser/`BYPASSRLS`) guarantees the policies apply *regardless of what the base login role is*. The GUC scopes *which* tenant; the role ensures the policy is *enforced at all*.

**Data-isolation guarantees:**

| | Before fix | After fix |
|---|---|---|
| GUC scope for statements 2..N of a request | reverted to `''` | correct `orgID` for the whole tx |
| Reads under RLS-subject role | 0 rows (broken) | correct tenant rows |
| Reads under privileged role | **all tenants (leak)** | app_user drop forces RLS → correct tenant rows |
| Writes | `WITH CHECK` fails / or cross-tenant if privileged | scoped to tenant, cross-tenant `INSERT` rejected |
| Pooled-conn cleanliness | GUC could persist (option A risk) | tx auto-reset returns conn clean |

**Edge cases:**
- *Aborted statement mid-request*: a failed `Exec` poisons the tx; `Release()` `Commit` fails and falls back to `Rollback` (`pool.go:111-125`) — no partial writes persist. Correct.
- *Double `Release()`*: guarded (`tx == nil` / `conn == nil` after first call). Safe.
- *`app_user` must hold table privileges*: RLS filters *within* granted privileges; if `app_user` lacks `SELECT/INSERT/...` grants the handler gets `permission denied`, not a leak. **Deployment dependency — see §1.5 and AC-15.6.** (`docker/init-db.sql` grants only `CONNECT`; grants are applied elsewhere — must be verified in the target env.)
- *Context cancellation*: if the request ctx is cancelled, the tx is rolled back by pgx; connection is released. Fail-closed.

**Potential regressions introduced by the fix:**
1. **Whole-request single transaction.** Previously each statement autocommitted; now all statements in a `WithTenant` request share one tx that commits only at `Release()`. Consequences: (a) longer-held row/advisory locks; (b) longer *idle-in-transaction* windows on slow handlers; (c) a late failure now rolls back *earlier* writes in the same request (more correct — atomic — but a behavioral change for any handler that previously relied on partial-commit). **Blast radius: every `WithTenant` call site** (documents, folders, dimensions, memberships, tags, orgs handlers — ~60 call sites).
   - **T2 deliverable (was a recommendation; elevated per PE review note #5):** set a bounded `idle_in_transaction_session_timeout` on the pool (via `pgxpool` `AfterConnect`, e.g. `SET idle_in_transaction_session_timeout = '15s'`) so a hung handler cannot pin a pooled connection open in a live transaction. This is a concrete build item, not advisory.
   - **New failure mode — external I/O inside a `WithTenant` tx (devils-advocate MEDIUM).** The single-tx-per-request model means any handler that interleaves DB queries with a slow external call (brain-api / OpenAI / Qdrant) now holds an open tx across that latency. Two consequences: (i) the tx can be killed by `idle_in_transaction_session_timeout` mid-request; (ii) atomic rollback of a late statement can orphan *non-transactional* external state already written (e.g. Qdrant vectors — re-ingest already appends duplicates). **T2 must audit `WithTenant` call sites for external I/O held inside tx scope, and pick the timeout against real handler latency** (`15s` is a placeholder — may be wrong in either direction). Where a handler must call out, prefer scoping the tx to just the DB work.
   - **Pool-starvation note (devils-advocate LOW).** Nested/fan-out `WithTenant` calls each pin a connection in an open tx; a handler holding ≥2 of `MaxConns` (25) risks pool-starvation deadlock under load. Pre-existing (the conn was pinned before), but the tx lengthens the hold — worth a call-site audit in T2.
2. **All request SQL now runs as `app_user`.** Any statement that needs a privilege `app_user` lacks will now fail where it previously ran as the (privileged) base role. This is the *intended* tightening but will surface missing grants.

### 1.4 Residual finding — `internal.go` GUC path without `SET ROLE` (APPROVED, in T2 scope)

`internal.go:105-117` (worker document-status callback) opens its own tx and sets the tenant GUC **but does not `SET LOCAL ROLE app_user`**. It is therefore *fail-open if and only if the base role is privileged* — precisely the case RED-18 forbids. Two defenses; both are now in scope:
- **Primary:** RED-18 startup assertion (§2) makes the base role RLS-subject, so this path is safe once RED-18 lands.
- **Defense-in-depth (§4.2 — TPM APPROVED, in this WO's build):** add `SET LOCAL ROLE app_user` to the `internal.go` tx for consistency with `WithTenant`. **This is a T2 deliverable** (PE review note #4 concurs: it is a one-line change in the same file family with identical rationale; folding it into T2 rather than a separate follow-up avoids leaving a latent fail-open path alive the moment any break-glass/privileged role is ever used). Route the product-code change to the principal-engineer via TPM (SA writes no product code).

### 1.5 Rollback plan (RED-15)

- The fix is a self-contained hunk in `pool.go`. If it causes a production regression (most likely cause: `app_user` missing table grants → handlers 500), **`git revert` only the `pool.go` hunk** of `2288221` (leaving the rest of the hardening intact) and redeploy. This reintroduces the isolation bug, so it is an *emergency* measure gated on RED-18 being active (which keeps reads fail-closed rather than fail-open).
- **Pre-deploy gate that avoids needing rollback:** confirm `app_user` holds `SELECT/INSERT/UPDATE/DELETE` on all tenant tables in the target DB *before* cutover (AC-15.6).

### 1.6 Acceptance criteria (RED-15)

A change is accepted only when all pass against a DB with `FORCE ROW LEVEL SECURITY` active and a **non-privileged** `app_user`:

- **AC-15.1 — GUC persists across statements.** Within one `WithTenant(orgID)` tx, run ≥2 sequential statements; assert `current_setting('app.current_tenant_id')` == `orgID` on *each* (proves the revert is gone).
- **AC-15.2 — Cross-tenant read isolation (actual rows under `app_user`).** Seed rows for org A and org B. A `WithTenant(A)` handler **returns ≥1 A row and exactly zero B rows** — the test must assert a *non-empty* A result, not merely "no B rows." (PE review: the prior test asserted only `current_user == app_user`; a query that returns zero rows for *both* orgs — e.g. under a missing-grant `permission denied` swallowed as empty — would have falsely "passed." A positive A-row count proves the fix works *and* that grants are present.)
- **AC-15.3 — Cross-tenant write rejection.** Under `WithTenant(A)`, an `INSERT` carrying `org_id = B` is rejected by the `WITH CHECK` policy (or forced to A); a legitimate `INSERT` under `WithTenant(A)` **succeeds** (proving `app_user` holds `INSERT` + sequence `USAGE`); no B-owned row is created.
- **AC-15.4 — Privilege drop.** Inside the tx, `SELECT current_user` == `app_user` (already asserted in `pool_integration_test.go:104`).
- **AC-15.5 — Atomic abort.** A statement that errors mid-request causes `Release()` to roll back; assert no earlier write from that request persisted.
- **AC-15.6 — Clean pooled connection.** After `Release()`, re-acquire the same physical connection; assert `current_user` == base role and the GUC is empty (no leak).
- **AC-15.7 — `app_user` table privileges present (self-contained deploy).** Against a DB provisioned **only** by the repo's migrations + `docker/init-db.sql` (no manual out-of-band grants), a `WithTenant(A)` read of a seeded tenant table **succeeds and returns rows** (does *not* raise `permission denied`). This proves the GRANT migration (§1.7) makes the landed RED-15 fix self-contained. (Blocking gap from design review — see §1.7.)
- **AC-15.8 — GRANT/RLS coverage reconciled (no unfiltered grant).** (a) **Coverage invariant:** every table on which `app_user` holds any DML privilege is either `relforcerowsecurity=true` **or** on the explicit documented global allowlist (`orgs`, `user_profiles`) — no other table is `app_user`-writable without RLS (guards current *and* future tables). (b) **Junction isolation:** under `WithTenant(A)`, a write to a junction table (`membership_*`, `document_tags`) that references a tenant-B `*_id` is rejected (by the §1.8 RLS policy under remedy (b), or by the documented app-layer ownership check under remedy (a)); a legitimate A-scoped junction write succeeds. (See §1.8; remedy is §4.4 decision.)

### 1.7 BLOCKING (design review) — `app_user` has no table privileges; the landed fix is a hard outage without a GRANT migration

**This is the one blocking implementation gap the principal-engineer elevated in design review; it is resolved here as a hard T2 deliverable.**

**The gap.** The landed RED-15 fix runs `SET LOCAL ROLE app_user` on **every** `WithTenant` request. `SET ROLE` in Postgres switches the *active* role and does **not** inherit the base login role's object privileges. But the repo grants `app_user` **only `CONNECT`**:

```sql
-- docker/init-db.sql:23-24 (the ONLY grants in the repo)
GRANT ALL PRIVILEGES ON DATABASE redarch_km TO app_admin;  -- app_admin (BYPASSRLS), not the runtime role
GRANT CONNECT ON DATABASE redarch_km TO app_user;          -- CONNECT only — no table/schema/sequence privileges
```

Verified in the current tree: **no `GRANT SELECT/INSERT/UPDATE/DELETE`, no schema `USAGE`, and no sequence `USAGE` for `app_user` exists in any migration (`001`–`003`) or in `init-db.sql`.** Consequence: on any environment provisioned solely from the repo, once the request drops to `app_user`, **every `WithTenant` handler fails with `permission denied`** for reads, writes, and sequence access. This is not "verify grants in the target env" — it is a functional hard outage the moment the landed fix is deployed to a clean DB.

**Why it was invisible.** `pool_integration_test.go` asserted only `current_user == app_user` (line 104). Any environment where the test DB happened to have grants applied out-of-band (e.g. tables owned by the connecting role, or CI-time grants) would pass, while a real deploy without that hidden setup breaks. The read-count assertions (`COUNT(*) FROM folders`) only pass because the test DB is pre-granted; nothing in the repo guarantees that on the target env.

**Resolution — required T2 deliverable: an idempotent GRANT migration** (`services/api-go/migrations/004_grant_app_user_tenant_privileges.up.sql`), applied by the migration role, granting `app_user` schema `USAGE`, DML on the tenant tables, and sequence `USAGE, SELECT`.

> **⚠ Do NOT use a blanket `GRANT … ON ALL TABLES`.** See §1.8 — 7 of the 18 tables have **no RLS policy**, so a blanket grant would hand `app_user` *unfiltered cross-tenant* DML on them. The grant MUST be reconciled with RLS coverage (§1.8). The block below is the safe shape; the exact table set and junction-table handling are specified in §1.8.

```sql
-- schema + sequence access
GRANT USAGE ON SCHEMA public TO app_user;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_user;  -- serial/identity INSERTs

-- DML on the 11 FORCE-RLS tenant tables (RLS filters WITHIN these privileges):
GRANT SELECT, INSERT, UPDATE, DELETE ON
  regions, departments, roles, groups, folders, tags,
  documents, document_access, document_attribute_definitions,
  chat_sessions, user_org_memberships
  TO app_user;

-- DML on the tenant-scoped junction tables written under WithTenant (see §1.8):
-- membership_regions, membership_departments, membership_roles, membership_groups, document_tags
-- >>> These MUST also receive FORCE RLS + a tenant policy in this same WO (§1.8), or the grant is a cross-tenant hole.

-- orgs, user_profiles: NOT granted to app_user — reached only via the privileged base pool today; documented global (§1.8).

-- ALTER DEFAULT PRIVILEGES: set for the SAME role that will create future tables (see §1.8 caveat) —
-- do NOT blanket future DML without the §1.8 coverage invariant, or new no-RLS tables silently become app_user-writable.
```

Notes for the implementer:
- **RLS is unaffected by these grants — for FORCE-RLS tables only.** RLS filters *within* granted privileges, so for the 11 FORCE-RLS tables (and any junction table given RLS per §1.8) granting DML does not weaken tenant isolation. **This reassurance does NOT hold for a table without an RLS policy** — there the grant is the *only* access control. See §1.8.
- **`ALTER DEFAULT PRIVILEGES` is scoped to the role that runs the migration** and applies to objects *subsequently* created by that role. It must be set for the actual migration-runner role — **verify who runs migrations** (docker entrypoints often run as `postgres`, not `app_admin`); if future migrations run as a different role, their new tables silently lack `app_user` DML → a future `permission denied` outage T9 won't catch. Pin the migration-runner-role invariant explicitly (§1.8 coverage gate).
- **Idempotent / re-runnable.** `GRANT`/`ALTER DEFAULT PRIVILEGES` are naturally idempotent; safe to re-apply.
- **Verified by AC-15.7 + test T9** (self-contained deploy) and **AC-15.8 + T10/T11** (junction-table isolation + coverage invariant, §1.8).

Without this migration the "already-landed" RED-15 fix is **not deployable**; with it (and §1.8), the fix is self-contained *and* does not open a new cross-tenant surface.

### 1.8 BLOCKING for T2 — reconcile the GRANT with RLS coverage; 7 tables have no policy (security-analyst + devils-advocate)

Both the security-analyst and the devils-advocate independently flagged this, and I verified it against the migrations. **Of the 18 tables, only 11 carry `FORCE ROW LEVEL SECURITY` + tenant policies** (`001:225-246`, `003`): `regions, departments, roles, groups, folders, tags, documents, document_access, document_attribute_definitions, chat_sessions, user_org_memberships`. **7 have no RLS policy at all:** `orgs`, `user_profiles`, and the junction tables `membership_regions`, `membership_departments`, `membership_roles`, `membership_groups`, `document_tags`.

For the 7 unprotected tables, an RLS grant is the *only* access control — there is no tenant predicate — so a blanket `GRANT … ON ALL TABLES` **removes the fail-closed `permission denied` net they have today** and lets a tenant-A request read/write tenant-B rows in them once it drops to `app_user`.

**This is reachable, not hypothetical, for the junction tables.** `AddMembership{Region,Department,Role,Group}` / `Clear*` (`memberships.go:548-591`) and `document_tags` INSERT/DELETE (`documents.go`) execute **directly under `WithTenant` (app_user)**. Those junction rows have **no `org_id`**, and FK integrity checks run with row-security off — so a body-supplied `role_id`/`region_id`/`tag_id` pointing at another tenant's row is **not** blocked by RLS. The moment the grant lands, any of those handlers that trusts a client-supplied `*_id` without an org-ownership check is a **cross-tenant IDOR write** on the membership/tag graph — exactly the authorization data RLS is meant to backstop. (`orgs`/`user_profiles` are latent by comparison: reached only via the privileged base pool today, e.g. `ListUsersInOrg` joins through RLS-forced `user_org_memberships` with an explicit org filter — but the grant would still strip their fail-closed net if a future `WithTenant` query touches them bare.)

**Required T2 resolution (do all three):**
1. **Scope, don't blanket.** Grant `app_user` DML explicitly on the 11 FORCE-RLS tables plus the junction tables it must write (per #2); do **not** grant `orgs`/`user_profiles` to `app_user` (leave them base-pool-only) and **document them as intentionally global**. Replace `ON ALL TABLES` with the explicit list.
2. **Give the tenant-scoped junction tables an RLS backstop.** Add `FORCE ROW LEVEL SECURITY` + a tenant policy to `membership_regions/departments/roles/groups` and `document_tags`. They lack `org_id`, so predicate via an `EXISTS` against the RLS-forced parent (e.g. membership rows visible only if their `membership_id`/`user_org_membership` is visible under the caller's tenant; `document_tags` via the parent `documents` row). This makes the FK/graph writes fail-closed at the DB, not only at the app layer. **[DECISION — see §4.4: remedy (b) add-RLS-now vs (a) accept app-layer-only.]**
3. **Add a coverage invariant (CI/test T11).** Assert that **every table `app_user` holds DML on is either `relforcerowsecurity=true` or on an explicit, documented global allowlist** (`orgs`, `user_profiles`). This is the check that would have caught this mismatch, and — paired with the `ALTER DEFAULT PRIVILEGES` note above — it guards every *future* table from silently becoming `app_user`-writable without RLS.

Also correct the §1.7 wording (done above): "grants don't weaken isolation" holds **only for FORCE-RLS tables.**

---

## 2. RED-18 — api-go runtime DB role must be non-superuser and non-`BYPASSRLS`

### 2.1 Current gap

The deploy invariant "the api-go process must connect as a role that is subject to RLS" is **undocumented and unenforced**. `docker/init-db.sql` ships two LOGIN roles:

```sql
CREATE ROLE app_user  LOGIN PASSWORD 'changeme';            -- RLS-subject (intended runtime role)
CREATE ROLE app_admin LOGIN PASSWORD 'changeme' BYPASSRLS;  -- for migrations / site-admin
```

Nothing prevents `DATABASE_URL` from pointing at `app_admin`, the bootstrap `postgres` superuser, or any future `BYPASSRLS` role. If it does:
- Every non-`WithTenant` query bypasses RLS — including `internal.go` (§1.4), health checks, and any future direct `pool.Query`.
- `WithTenant` paths survive (they `SET ROLE app_user`), which *masks* the misconfiguration in testing and makes it more dangerous in production.

There is no startup check (`grep -rn "rolsuper\|BYPASSRLS\|pg_roles" services/api-go` → no matches). This is the single most consequential unenforced invariant behind the tenant boundary.

### 2.2 Fix approach

Add a **fail-fast startup assertion** in api-go that runs once, immediately after the pool is created and *before* the HTTP listener binds. Implement as an exported, unit-testable function in the `db` package:

```go
// AssertNonPrivilegedRole verifies the pool's login role is subject to RLS.
// It fails fast so a misconfigured DATABASE_URL can never serve tenant data
// with RLS disabled. pg_roles is world-readable and exposes a role's own
// rolsuper/rolbypassrls, so app_user can run this without elevated privileges.
func (p *Pool) AssertNonPrivilegedRole(ctx context.Context) error {
    var role string
    var isSuper, bypassRLS bool
    err := p.QueryRow(ctx,
        `SELECT current_user, rolsuper, rolbypassrls
           FROM pg_roles WHERE rolname = current_user`,
    ).Scan(&role, &isSuper, &bypassRLS)
    if err != nil {
        return fmt.Errorf("assert db role: %w", err)
    }
    if isSuper || bypassRLS {
        return fmt.Errorf(
            "REFUSING TO START: api-go DB role %q is privileged "+
                "(superuser=%v, bypassrls=%v); RLS tenant isolation would be "+
                "disabled. Point DATABASE_URL at a non-superuser, non-BYPASSRLS "+
                "role (e.g. app_user)", role, isSuper, bypassRLS)
    }
    slog.Info("db runtime role verified RLS-subject", "role", role)
    return nil
}
```

Wire in `cmd/server/main.go run()` right after `db.NewPool` succeeds. **Pool creation is inside the `if cfg.DatabaseURL != ""` block (`main.go:66-67`), so the assertion MUST go inside that same block** — placing it outside would nil-deref `pool` when DB features are disabled (PE review note #1):

```go
if cfg.DatabaseURL != "" {
    pool, err = db.NewPool(ctx, cfg.DatabaseURL)
    if err != nil { return fmt.Errorf("create db pool: %w", err) }
    defer pool.Close()
    if err := pool.AssertNonPrivilegedRole(ctx); err != nil {
        return err // run() returns → main() logs + os.Exit(1), before serving
    }
}
```

**Design notes / why this query:**
- **Attribute check, not name check.** `rolsuper OR rolbypassrls` is authoritative; a name allowlist (`current_user = 'app_user'`) is brittle and wrong across environments. Superuser is checked explicitly because superusers bypass RLS irrespective of the `rolbypassrls` flag.
- **`rolbypassrls` is not inherited.** RLS bypass depends on the *active* role's own attribute, not on role membership; so checking `current_user` (the login role, before any `SET ROLE`) exactly matches the session's real RLS behavior. Documented follow-on constraint: no connection-level default `SET ROLE`/`role=` in `DATABASE_URL` should silently escalate — out of scope to enforce here, noted for ops.
- **Complementary deploy constraint — the base role must be able to *assume* `app_user` (design-review carry-forward C2, requirements-auditor).** `WithTenant` runs `SET LOCAL ROLE app_user` on every request, which requires the login role to *be* `app_user` or hold **membership** in it. `AssertNonPrivilegedRole` deliberately checks only "non-super AND non-`BYPASSRLS`" (the two RED-18 attributes) — that is **strictly weaker** than what `WithTenant` needs. A legitimate non-privileged role that is *not* a member of `app_user` would **pass** RED-18 yet fail *every* `WithTenant` call with `permission denied to set role "app_user"`. The intended and primary deploy (`DATABASE_URL=app_user`, where `SET ROLE app_user` is a no-op) is fully covered by AC-15.7/T9, so this is not a RED-18 blocker; it is closed by a documented deploy constraint (§5, `docs/DEPLOYMENT.md`) and **AC-18.6**. *Optional* (out of scope for this WO, noted only): the assertion could additionally verify `pg_has_role(current_user, 'app_user', 'USAGE')` to fail-fast on this misconfiguration — a small future hardening, not required here.
- **No elevated privilege needed.** `pg_roles` is a public view; a role can always read its own `rolsuper`/`rolbypassrls`. So the assertion works as `app_user`.
- **Runs before serving.** Placed before listener bind so a misconfigured deploy crash-loops at boot (visible in orchestrator health) rather than serving un-isolated data.

**Break-glass override (rollback valve) — TPM APPROVED (§4.1), in T2/T3 scope:** support an env flag `KM_ALLOW_PRIVILEGED_DB_ROLE=1` that downgrades the hard failure to a `WARN` log and continues. Default is **hard-fail**. This exists solely so an operator can start the service during an incident if the check ever false-positives. Hardening (security-analyst + devils-advocate): **parse strictly `=="1"`** (not any-nonempty), **env-only** (no config-file/DSN path), **emit the WARN on every startup while set** (not once), and — because in this mode the non-`WithTenant` paths (`internal.go`, health, future direct `pool.Query`) are *actively* fail-open cross-tenant — **emit an ongoing degraded-health / metric signal, not just a boot log**, so a valve left on after an incident is continuously visible.

### 2.3 Threat model / guarantees

| Deploy misconfiguration | Before RED-18 | After RED-18 |
|---|---|---|
| `DATABASE_URL` = `app_admin` (BYPASSRLS) | serves; non-`WithTenant` paths leak cross-tenant | process refuses to start, exit ≠ 0 |
| `DATABASE_URL` = `postgres` (superuser) | serves; RLS fully bypassed | process refuses to start, exit ≠ 0 |
| `DATABASE_URL` = `app_user` | serves | serves (verified line logged) |

Blast radius of the change itself is limited to startup; no request-path behavior changes. Scope note: `worker-go` and `brain-api-go` share the same RLS invariant and should adopt the same `AssertNonPrivilegedRole` — **DEFERRED to a follow-up WO per §4.3 (out of scope here).**

### 2.4 Rollback plan (RED-18)

- **Preferred (no rollback):** the break-glass env flag (`KM_ALLOW_PRIVILEGED_DB_ROLE=1`) lets an operator start the service if the assertion ever blocks a legitimate deploy, without shipping code. Emits WARN.
- **Code rollback:** the assertion is a single call site in `main.go` plus one function in `db`. `git revert` the RED-18 commit restores prior behavior. Low blast radius, no schema/data change to undo.

### 2.5 Acceptance criteria (RED-18)

- **AC-18.1 — Fails on superuser.** `AssertNonPrivilegedRole` returns a non-nil error when connected as a superuser role, and that error names the offending role and both flags (`superuser=…, bypassrls=…`). At process level (verified by inspection of the §2.2 wiring, not a full-boot test) this propagates as `run()` returning → `main` exits non-zero *before binding the listener*.
- **AC-18.2 — Fails on BYPASSRLS.** Same as AC-18.1 with a non-superuser `BYPASSRLS` role (e.g. `app_admin`).
- **AC-18.3 — Passes on safe role.** With `DATABASE_URL` = `app_user` (non-super, non-bypassrls), startup completes and the server serves; the "role verified RLS-subject" line is logged.
- **AC-18.4 — No privilege required.** The assertion query succeeds when connected as `app_user` (proves it reads the role's own `pg_roles` row without elevation).
- **AC-18.5 — Break-glass.** With a privileged role *and* `KM_ALLOW_PRIVILEGED_DB_ROLE=1`, the process starts but logs a WARN naming the risk. (Valve APPROVED — §4.1.)
- **AC-18.6 — Base role can assume `app_user` (carry-forward C2).** With `DATABASE_URL` = the intended runtime role, a `WithTenant` call succeeds in executing `SET LOCAL ROLE app_user` (i.e. the role *is* `app_user` or holds membership in it) — it does **not** raise `permission denied to set role "app_user"`. Satisfied trivially when `DATABASE_URL=app_user`; the `docs/DEPLOYMENT.md` constraint (§5) documents the membership requirement for any other non-privileged runtime role.

**Test strategy (PE review note #2).** AC-18.1/18.2 phrased as "process exits before bind" are painful and slow to exercise at process level. Prefer **unit-testing `AssertNonPrivilegedRole` directly** against a live DB (gated on `DATABASE_URL`, same pattern as `pool_integration_test.go`): connect as a superuser/`app_admin` role → expect a non-nil error; connect as `app_user` → expect nil (this also covers AC-18.4, "no privilege required"). Cover AC-18.5 by unit-testing the caller's downgrade branch with the env flag set. Document the startup invariant with a one-line note in `docs/DEPLOYMENT.md` rather than attempting a full-boot exit-code test.

---

## 3. Combined test matrix

| Test | Role | Path | Expect |
|---|---|---|---|
| T1 | app_user | `WithTenant(A)` read of A+B data | **≥1 A row** and 0 B rows (AC-15.2) |
| T2 | app_user | `WithTenant(A)` write org_id=B / legit write org_id=A | B rejected, A succeeds (AC-15.3) |
| T3 | app_user | 2 statements in one tx | both scoped to A (AC-15.1) |
| T4 | app_user | mid-tx error then Release | rollback, no partial write (AC-15.5) |
| T5 | app_user | conn reuse after Release | clean role + empty GUC (AC-15.6) |
| T6 | superuser | startup / `AssertNonPrivilegedRole` | error, exit ≠ 0 (AC-18.1) |
| T7 | app_admin (BYPASSRLS) | startup / `AssertNonPrivilegedRole` | error, exit ≠ 0 (AC-18.2) |
| T8 | app_user | startup / `AssertNonPrivilegedRole` | nil, serves (AC-18.3/18.4) |
| **T9** | **app_user** | **repo-only-provisioned DB (migrations + init-db, no manual grants): `WithTenant(A)` read** | **succeeds, returns rows — no `permission denied` (AC-15.7); proves the §1.7 GRANT migration is self-contained** |
| **T10** | **app_user** | **`WithTenant(A)` junction write (`membership_*`/`document_tags`) referencing a tenant-B `*_id`; and a legit A-scoped junction write** | **B-referencing write rejected, A write succeeds (AC-15.8b, §1.8)** |
| **T11** | **(schema/CI)** | **coverage invariant: enumerate every table `app_user` has DML on** | **each is `relforcerowsecurity` or on the documented global allowlist (`orgs`,`user_profiles`) (AC-15.8a)** |
| **T12** | **app_user** | **`internal.go` callback path (after §4.2 `SET LOCAL ROLE app_user`): cross-tenant read** | **only caller-tenant rows (defense-in-depth, §1.4/§4.2)** |

Integration tests are gated on a live Postgres with FORCE RLS (pattern already in `internal/db/pool_integration_test.go` and `internal/handlers/internal_integration_test.go`). **T9 must run against a DB built solely from the repo's migrations + `docker/init-db.sql`** — do not pre-grant `app_user` out-of-band, or the test cannot detect the blocking gap it exists to guard. T1/T2 must assert *positive* A-row results (not just the absence of B rows) so a missing-grant `permission denied` cannot masquerade as a passing isolation test. **T11 is the coverage gate that would have caught the §1.8 GRANT/RLS mismatch; it must run in CI, not only locally.**

---

## 4. Decisions — RESOLVED by TPM (2026-07-06)

None of these change user-facing UX/workflow/layout/behavior. All three were flagged as owner decisions in REV 1; the TPM has now decided them. Recorded here as **decided**, not open:

1. **§4.1 — Break-glass env flag `KM_ALLOW_PRIVILEGED_DB_ROLE`** (§2.2): **APPROVED — include it; default HARD-FAIL; WARN on use.** It is an ops incident valve. In T3 scope. (SA recommendation and PE review both concurred: include, hard-fail default, WARN.)
2. **§4.2 — Add `SET LOCAL ROLE app_user` to `internal.go`** (§1.4, defense-in-depth): **APPROVED — in scope for this WO's build.** Folded into **T2** (PE review note #4 concurs it belongs in T2, not a separate follow-up). Product-code change routed to the principal-engineer via TPM.
3. **§4.3 — Same `AssertNonPrivilegedRole` assertion for `worker-go` / `brain-api-go`** (§2.3): **DEFERRED to a follow-up WO — out of scope here.**
4. **§4.4 — Junction-table isolation remedy (NEW — raised by security-analyst + devils-advocate, §1.8): OPEN, needs owner sign-off.** The §1.8 fix requires handling the 5 tenant-scoped junction tables (`membership_regions/departments/roles/groups`, `document_tags`) that get `app_user` DML but have no RLS. Two remedies:
   - **(b) Add `FORCE RLS` + a tenant policy** (via `EXISTS` against the RLS-forced parent) to each junction table in this WO — makes cross-tenant junction writes fail-closed at the DB. **SA recommendation:** remedy (b) — it is the WO's own thesis (Postgres RLS is the tenant backstop), it closes a real cross-tenant IDOR write vector, and it is a contained additive migration. Cost: 5 new policies + tests; mild write-path overhead from the `EXISTS` predicate.
   - **(a) Accept app-layer-only** ownership validation on those handlers and document the tables as app-layer-scoped. Cheaper, but leaves the DB fail-open on those tables and relies entirely on every current/future handler validating `*_id` org-ownership. Not recommended for a tenant-isolation hardening WO.
   This is a trust-boundary + scope decision (adds schema/policy work); flagged to TPM/owner. Either way, **§1.8 items #1 (scoped grant) and #3 (coverage invariant) are required regardless** — only the junction backstop (#2) is the (a)/(b) choice.

All product-code implementation is routed to the principal-engineer through the TPM (SA writes no product code).

---

## 5. Build handoff

**T2 (RED-15 completion — the landed `pool.go` fix is NOT self-contained until these ship):**
1. **GRANT migration, RLS-reconciled (BLOCKING — §1.7 + §1.8):** new idempotent `migrations/004_grant_app_user_tenant_privileges.up.sql` (+ `.down.sql`). **Scoped, not blanket:** DML on the explicit 11 FORCE-RLS tables + the junction tables written under `WithTenant`; schema `USAGE`; sequence `USAGE, SELECT`; `ALTER DEFAULT PRIVILEGES` for the correct migration-runner role. Without this, every `WithTenant` handler returns `permission denied` on a repo-provisioned DB.
2. **Junction-table RLS backstop (BLOCKING — §1.8 #2, remedy per §4.4 decision):** `FORCE RLS` + tenant policy on `membership_regions/departments/roles/groups` + `document_tags` (predicated via `EXISTS` against the RLS-forced parent), or the §4.4-(a) documented app-layer path. `orgs`/`user_profiles` left base-pool-only + documented global.
3. **Coverage invariant (BLOCKING — §1.8 #3, test T11 in CI):** every `app_user`-DML table is `relforcerowsecurity` or on the documented global allowlist.
4. **AC-15 isolation tests (T1–T5, T9, T10, T12) — load-bearing, not optional** (PE review note #3). Assert *positive* A-row results; T9 on a repo-only-provisioned DB; T10 junction cross-tenant; T12 `internal.go` path.
5. **`internal.go` defense-in-depth (§1.4 / §4.2 APPROVED):** add `SET LOCAL ROLE app_user` to its tx.
6. **`idle_in_transaction_session_timeout` on the pool (§1.3):** concrete deliverable via `pgxpool` `AfterConnect`; **audit `WithTenant` call sites for external I/O held inside tx (§1.3)** and tune the timeout to real latency.
- **RED-18 (T3):** net-new — `Pool.AssertNonPrivilegedRole` in `internal/db/pool.go`, wired **inside** the `if cfg.DatabaseURL != ""` block in `cmd/server/main.go` (§2.2), the `KM_ALLOW_PRIVILEGED_DB_ROLE` break-glass valve (§4.1 APPROVED), unit tests per §2.5 strategy (T6–T8), and a `docs/DEPLOYMENT.md` note stating **both** invariants: (1) the api-go runtime role must be non-superuser and non-`BYPASSRLS`; (2) it must **be `app_user` or hold membership in `app_user`** so `WithTenant`'s `SET LOCAL ROLE app_user` succeeds (carry-forward C2 / AC-18.6).

**Critical path for the security gate:** the **§1.7 GRANT migration** (RED-15 is otherwise undeployable) and **RED-18** (converts the whole non-`WithTenant` surface from fail-open to fail-closed).
