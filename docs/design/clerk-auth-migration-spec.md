# Design Spec — Knowledge Manager auth migration: Keycloak → Clerk

- **Work Order:** WO-2026-06-14-knowledge-manager-rewrite-cont
- **Author:** solution-architect (design only — no code, no commits)
- **Status:** ✅ **APPROVED 2026-06-24** — human ratified the migration and all §9 decisions at the SA-recommended values (D2 on-prem→cloud explicitly accepted). Build contract + per-slice acceptance criteria in **§14**. Two mandatory guardrails: **R2** (azp allowlist + security-analyst review of verify path) and **R6** (add Go CI test job).
- **Date:** 2026-06-24
- **Human directive:** *"I want the auth to be changed to use Clerk."*
- **Governing principle (ratified, e65):** Go-first; keep Python only where it has ideal native modules.

> This is the build contract for the principal-engineer. It does **not** authorize any
> code change on its own. §9 ("Decisions for the human") must be answered first; several
> answers change the blast radius and the phase plan.

---

## 1. Executive summary

The Knowledge Manager authenticates **end users** with self-hosted **Keycloak** (OIDC/JWT,
realm `redarch`, client `redarch-km`). Both backend stacks (Python `services/api`, Go
`services/api-go`) are **resource servers**: they verify a Keycloak-issued RS256 JWT via
JWKS and check issuer + audience. They issue **no tokens** — no login/refresh/logout
endpoints exist server-side; the SPA talks to Keycloak directly. Service-to-service auth
(brain-api, worker) uses **separate shared secrets** (`BRAIN_API_KEY`, `INTERNAL_API_KEY`)
and is **not** Keycloak-coupled.

**The migration is fundamentally a token-verifier swap plus a frontend SDK swap.** The
32-bit `access_mask` RBAC is **100% database-backed** and derived from
`user_org_memberships` dimension assignments — it is **never** computed from IdP
roles/claims. So the RBAC model is *orthogonal to the IdP* and does **not** change. The
only identity coupling in the data model is one column: `user_profiles.keycloak_sub`
(stores the IdP subject).

**Recommendation (the key decision, §3):** wire Clerk into the **Go `api-go` stack + the
Next.js UI** as the canonical path; treat the Python `services/api` user-auth as a legacy
parallel implementation and do a **light parity port only** (it keeps CI green and dev
parity). Service-to-service secrets stay **unchanged**.

**Material flags for the human (§9):** (a) Keycloak is **self-hosted/on-prem**; Clerk is a
**cloud SaaS** — this moves user identity + PII to a third party (data-residency, air-gap,
availability, vendor lock-in, cost). (b) Keycloak `sub` ≠ Clerk `sub`, so **existing users
must be remapped** or memberships orphan. (c) Go vs both stacks. (d) hard cutover vs
dual-verify coexistence.

---

## 2. Inventory — current Keycloak integration points

### 2.1 UI (Next.js, `ui/`)

| Concern | Today | File |
|---|---|---|
| Library | `keycloak-js` 26.2.0 (direct OIDC, PKCE S256) | `ui/package.json` |
| Init / login | `Keycloak({url,realm,clientId})`, `init({onLoad:"login-required"})` → redirect to Keycloak | `ui/src/lib/auth/keycloak.ts:15-37` |
| Token access | in-memory `getKeycloak().token`; `refreshToken(minValidity)` = `updateToken` | `ui/src/lib/auth/keycloak.ts:39-45` |
| Token → API | axios request interceptor: `refreshToken(30)` then `Authorization: Bearer <token>` + `X-Org-ID` | `ui/src/lib/api/client.ts:19-40` |
| Streaming | manual `fetch()` for `/search/chat/stream` attaches same Bearer + `X-Org-ID` | `ui/src/lib/api/search.ts:54-84` |
| 401 handling | response interceptor → `window.location = "/login"` | `ui/src/lib/api/client.ts:49-69` |
| Auth context | `AuthProvider` calls `initKeycloak()`, exposes `useAuth()` (isAuthenticated, username, email, logout) | `ui/src/context/AuthContext.tsx:17-45`; wrapped in `ui/src/app/layout.tsx` |
| Route protection | **No** `middleware.ts`. Client gate: `(authenticated)/layout.tsx` redirects to `/login` via `useAuth()` | `ui/src/app/(authenticated)/layout.tsx:18-34` |
| Login page | redirect-only placeholder ("Redirecting to sign-in…") | `ui/src/app/(auth)/login/page.tsx` |
| Logout | `getKeycloak().logout()` (clears `redarch:currentOrgId` first) | `ui/src/lib/auth/keycloak.ts:47-54`; `Header.tsx:18` |
| Sign-up | none in app (Keycloak hosted registration) | — |
| Env | `NEXT_PUBLIC_KEYCLOAK_URL/REALM/CLIENT_ID`, `NEXT_PUBLIC_API_URL` | `ui/src/lib/auth/keycloak.ts:18-20` |

### 2.2 Go backend (`services/api-go`) — canonical user API

| Concern | Today | File |
|---|---|---|
| Router | `go-chi/chi/v5`; JWT middleware wraps `/api/*` | `cmd/server/main.go:122-131` |
| Verify | `lestrrat-go/jwx/v2`: `jwt.Parse(..., WithKeySet, WithValidate, WithIssuer, WithAudience(ClientID))`, RS256, JWKS cached ~5 min | `internal/middleware/auth.go:57-153` |
| JWKS/issuer | `KeycloakURL + /realms/{realm}/protocol/openid-connect/certs`; issuer `KeycloakURL + /realms/{realm}` | `internal/middleware/auth.go:57-67` |
| Config | `KeycloakURL/Realm/ClientID` from `KEYCLOAK_URL/REALM/CLIENT_ID`; URL mandatory in prod | `internal/config/config.go:31-34,61-85` |
| Claims used | `sub`, `email`, `preferred_username`, `name` | `internal/middleware/auth.go:131-151` |
| User provisioning | upsert `user_profiles` by `keycloak_sub` on first call | `internal/handlers/users.go` |
| Login/refresh/logout | **none** (Keycloak handles client-side) | — |
| Status | **fully implemented**, incl. mask-based folder filtering | `internal/handlers/folders.go:149-162,338-390` |

### 2.3 Python backend (`services/api`)

| Concern | Today | File |
|---|---|---|
| Verify | `validate_keycloak_token()` — JWKS fetch+cache(300s), `jwt.decode(..., RS256, audience=client_id, issuer={url}/realms/{realm})` | `services/api/src/api/auth/keycloak.py:19-69` |
| Entry | `get_current_user()` FastAPI dep: bearer → validate → provision; **plus E2E bypass** (`X-Test-User`/`X-Test-Secret`) | `services/api/src/api/auth/dependencies.py:90-145`, bypass `:50-87` |
| Config | `keycloak_url/realm/client_id`; `secret_key` (E2E only), `e2e_test_mode/secret` | `services/api/src/api/config.py` |
| Claims used | `sub`, `preferred_username`, `email` | `dependencies.py` |
| Provisioning | `provision_user_from_claims()` find/create `user_profiles` by `keycloak_sub`, sync username/email | `services/api/src/api/services/user_provisioning.py:15-54` |
| `/me` | returns CurrentUser; **no** login/refresh/logout endpoints | `services/api/src/api/routers/auth.py` |

### 2.4 RBAC / `access_mask` — IdP-independent (no change)

- 32-bit layout `[ORG_ID(11)][REGION(5)][ROLE(5)][GROUP(7)][DEPT(4)]`; **no** READ/WRITE/ADMIN flags — purely *dimensional*. (`packages/access_mask/src/.../constants.py`, `packages/accessmask/mask.go`)
- Mask is computed from **DB** `user_org_memberships` → `membership_{regions,departments,roles,groups}` (each dimension row has `permission_number`), via the Cartesian product in `permission_config.py:80-102` (Py) / `internal/services/permissions.go:24-72` (Go). **IdP roles never feed the mask.**
- First login: `user_profiles` created with `is_site_admin=false` and **no membership** (admin grants access later).
- **Sole identity coupling:** `user_profiles.keycloak_sub` (the IdP subject). Everything downstream keys on the internal `user_profiles.id` UUID.

### 2.5 Service-to-service auth — not Keycloak (no change)

- `BRAIN_API_KEY` → `X-API-Key` (api/worker → brain-api). `INTERNAL_API_KEY` → `X-Internal-API-Key` (worker → api callbacks). Separate shared secrets by design. Verified in both stacks (`brain_api/auth.py`, `brain-api-go/cmd/server/main.go:187-213`, `dependencies.py:218-240`). **Recommendation: leave entirely as-is.** (Go note: the `POST /api/internal/documents/{id}/status` handler is not yet implemented on `api-go` — pre-existing gap, out of scope here.)

### 2.6 Deployment / canonical-stack evidence

- `docker-compose.go.yml` wires `api-go:8000`, sets `KEYCLOAK_URL` on it, and points the UI `NEXT_PUBLIC_API_URL` → the Go API. `docker-compose.yml` wires the Python stack on the same port (mutually exclusive). `go.work` builds the 5 Go modules.
- **CI (`/.github/workflows/ci.yml`) tests only Python + UI type-check + Playwright(bypass). There is *no* Go test job.** ⇒ Python is the CI-covered reference impl; Go is canonical-by-deploy but **not CI-gated**. (See risk R6.)

---

## 3. RECOMMENDATION — which stack gets Clerk

**Recommend: Go `api-go` + UI as the canonical target; Python `services/api` = light parity port only.** Rationale:

1. **Go-first principle (ratified).** Auth is generic verification with a first-class Clerk Go SDK — no "ideal native Python module" justifies keeping it in Python. This is exactly the Go-first lane.
2. **Go is the deployed user-facing API.** The canonical compose points the UI at `api-go` and configures Keycloak there; `api-go` auth (incl. mask filtering) is fully implemented.
3. **But Python is the only CI-tested backend today.** A *light* Python port (the auth module is a ~1-file swap, §4.3) keeps CI green and the Python dev compose usable at near-zero marginal cost. It is **not** a second product surface — just parity.
4. **YAGNI guard:** if the human confirms `services/api` (Python user API) is being **retired** in favor of `api-go`, **drop the Python port entirely → pure Go-only.** That is Decision **D1**.

> Net: **Go + UI mandatory; Python parity = cheap insurance, droppable on D1.** Service-to-service auth untouched in all stacks.

---

## 4. Clerk target design (per integration point)

### 4.0 Clerk verification model (grounding the design)

Clerk session tokens are **RS256 JWTs**. Verification differs from Keycloak in two ways the
implementer must honor:

- **Issuer (`iss`)** = your Clerk **Frontend API URL** (prod custom domain `https://clerk.<domain>` or dev `https://<slug>.clerk.accounts.dev`). **JWKS** at `{iss}/.well-known/jwks.json` (or `https://api.clerk.com/v1/jwks`).
- **No `aud` claim by default.** Keycloak's audience check (`client_id`) has **no direct equivalent**. The security-critical replacement is verifying **`azp` (authorized party)** against an allowlist of frontend origins → **`CLERK_ALLOWED_AZP`**. *Skipping the azp check is a security defect* (token-origin confusion). Optionally, a Clerk **JWT template** can mint an `aud`, but default-token + `azp` is the standard path.
- Tokens are **short-lived (~60s)** and auto-refreshed by the Clerk frontend SDK. Backend stays a **stateless verifier** (same shape as today).

Sources: Clerk manual JWT verification, clerk-sdk-go/v2 `jwt.Verify`, clerk-backend-api `authenticate_request`, @clerk/testing (see §8).

### 4.1 Environment variables

| Remove (Keycloak) | Add (Clerk) | Where |
|---|---|---|
| `KEYCLOAK_URL`, `KEYCLOAK_REALM`, `KEYCLOAK_CLIENT_ID` | `CLERK_SECRET_KEY` (`sk_…`), `CLERK_JWT_ISSUER` (Frontend API URL) or `CLERK_JWT_KEY` (PEM, for networkless verify), `CLERK_ALLOWED_AZP` (comma-list of UI origins) | backend (Go + Py), `.env.example`, both compose files |
| `NEXT_PUBLIC_KEYCLOAK_URL/REALM/CLIENT_ID` | `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` (`pk_…`), `CLERK_SECRET_KEY` (server, for `@clerk/nextjs`), optional `NEXT_PUBLIC_CLERK_SIGN_IN_URL`/`SIGN_UP_URL` | UI |

- **Keep unchanged:** `BRAIN_API_KEY`, `INTERNAL_API_KEY`, `API_SECRET_KEY` (E2E-only signing secret), `API_E2E_TEST_MODE`/`E2E_TEST_SECRET`, `NEXT_PUBLIC_API_URL`.

### 4.2 Go `api-go`

- **Dep:** `github.com/clerk/clerk-sdk-go/v2`. Replace `lestrrat-go/jwx` JWT path in `internal/middleware/auth.go`.
- **Verify:** either `clerkhttp.WithHeaderAuthorization()` middleware, or call `jwt.Verify(ctx, &jwt.VerifyParams{Token: tok, /* networkless if JWK provided */})`. Set **authorized parties** from `CLERK_ALLOWED_AZP`. JWKS cached (SDK default ~1h) or networkless via `CLERK_JWT_KEY`.
- **Claims:** `sub` (Clerk user id `user_…`) → provisioning subject. `email` via session claims / `email` claim if present (Clerk default session token carries limited claims; if `email`/`username` are needed at verify-time, add them via a **JWT template** or fetch via Backend API on provisioning — see §7). `name`/`preferred_username` map to Clerk's available fields; absent ones default gracefully.
- **Config:** swap `Keycloak*` fields → `ClerkIssuer/ClerkAllowedAZP/ClerkSecretKey`; keep prod-mandatory validation.
- **Untouched:** mask filtering, handlers, sqlc queries (subject column handling per §6).

### 4.3 Python `services/api` (parity port; skip on D1=retire)

- **Dep:** `clerk-backend-api` (PyPI) **or** `PyJWT` + `PyJWKClient` against Clerk JWKS.
- Replace `auth/keycloak.py` with `auth/clerk.py`: `clerk.authenticate_request(httpx.Request, AuthenticateRequestOptions(authorized_parties=[…]))` → `request_state.payload` (claims incl. `sub`), or manual JWKS verify with explicit `azp` check.
- `dependencies.py:get_current_user`: same shape; **E2E bypass block stays byte-for-byte** (it's IdP-agnostic — see §8). `provision_user_from_claims` unchanged except claim sources.

### 4.4 UI (`@clerk/nextjs`)

| Surface | Change |
|---|---|
| Dep | remove `keycloak-js`; add `@clerk/nextjs` |
| Provider | `ui/src/app/layout.tsx`: wrap with `<ClerkProvider>` (replaces `AuthProvider`); keep `OrgProvider` inside |
| Middleware | **new** `ui/src/middleware.ts` with `clerkMiddleware()` + matcher (real route protection — an improvement over today's client-only gate) |
| Sign-in/up | replace `(auth)/login/page.tsx` redirect-stub with Clerk `<SignIn/>` (and `<SignUp/>` route) or hosted pages |
| Auth state | `useAuth()`/`useUser()` from Clerk replace custom `AuthContext`; `(authenticated)/layout.tsx` gate uses `isSignedIn` (or `auth.protect()` in middleware) |
| Token → API | **integration-critical:** API calls are **cross-origin** (`:3000`→`:8000`), so Clerk's same-origin cookie is insufficient → must attach `Authorization: Bearer <getToken()>`. Replace `getKeycloak().token`/`refreshToken()` in `client.ts` and `search.ts` with Clerk `getToken()` (client comp: `useAuth().getToken()`; non-React axios module: `window.Clerk?.session?.getToken()`). `azp` must include the UI origin; CORS already allows it. |
| Logout | `Header.tsx`: `useClerk().signOut()` (still clear `redarch:currentOrgId` first) or `<UserButton/>` |
| 401 redirect | keep, target Clerk sign-in URL |

### 4.5 Logout / refresh / sign-up

- **Refresh:** Clerk auto-refreshes short-lived tokens; the manual `refreshToken(30)` pattern collapses into `getToken()`. **Logout:** `signOut()`. **Sign-up:** now in-app via `<SignUp/>` (was Keycloak-hosted) — *this is a user-facing flow change, see Decision D5.*

---

## 5. Blast radius

| Surface | Impact |
|---|---|
| Frontend (`ui/`) | **High** — provider, middleware(new), sign-in/up, token attach (axios + streaming fetch), logout, env. |
| Backend Go (`api-go`) | **Medium** — `internal/middleware/auth.go`, `internal/config/config.go`, `cmd/server/main.go`, go.mod. |
| Backend Python (`api`) | **Low** (parity) or **none** (D1=retire) — `auth/clerk.py`, `config.py`, `pyproject.toml`. |
| DB | **Medium** — `user_profiles.keycloak_sub` rename/repurpose + **user re-mapping migration** (§6, D3). RBAC tables/`access_mask` **untouched**. |
| Infra/env | `.env.example`, `docker-compose.yml` + `docker-compose.go.yml` (`KEYCLOAK_URL`→Clerk), remove `keycloak` service at teardown phase. |
| Service-to-service | **None.** |
| Docs/CI | `.github/workflows/ci.yml` (Clerk test env, optional new Go job — R6), this spec, README. |

---

## 6. Identity column & user data migration (the real risk)

`user_profiles.keycloak_sub` is unique-not-null and is the join from IdP identity → internal user. **Keycloak `sub` (a UUID) ≠ Clerk `sub` (`user_…`)**, so a naive swap orphans every existing membership.

**Column:** recommend rename `keycloak_sub` → `auth_subject` (provider-neutral) in a forward migration; or keep the column name and just store Clerk subs (zero-DDL, but misleading). *Naming is cosmetic; the data remap is the substance.* — **Decision D3.**

**Remap strategy options:**
- **(A) Greenfield** — drop/ignore old subjects; all users re-register; admins re-grant memberships. Cheapest; acceptable only if the user base is dev/throwaway.
- **(B) Email relink (recommended for an internal tool)** — on first Clerk login, if no row matches the Clerk `sub`, match on **verified** Clerk email to an existing `user_profiles.email` and rebind `auth_subject`. Preserves memberships. *Guard: only relink Clerk-verified emails; one-time; log it.*
- **(C) Bulk import** — import existing users into Clerk (Clerk user-import API/CLI), pre-seed the sub map. Most faithful, most effort.

---

## 7. Claims availability note (implementer must confirm at T3)

Clerk's **default** session token carries a minimal claim set (`sub`, session/org claims) — `email`/`username` may **not** be present without a **JWT template**. Provisioning needs `email` (unique-not-null) + `username`. Two clean options: (i) configure a Clerk **JWT template** to include `email`/`username` (verify-time, no extra call); or (ii) on first provision, fetch the user via the **Clerk Backend API** using `CLERK_SECRET_KEY`. Recommend **(i)** for the common path. AC-B4/AC-P3 below gate this.

---

## 8. Test migration

**Good news: most tests are IdP-agnostic already.** The Python unit/integration tests **and** the Playwright e2e suite authenticate via the **`X-Test-User`/`X-Test-Secret` bypass** (gated by `API_E2E_TEST_MODE`), *not* by minting Keycloak tokens. The `access_mask` unit tests have no auth. So:

| Test category | Today | Clerk migration |
|---|---|---|
| `access_mask`/`accessmask` unit | no auth | **no change** |
| Python unit/integration (`dependencies.py` bypass, RLS tests) | `X-Test-User`+`X-Test-Secret` | **no change** (bypass stays; keep block byte-for-byte) |
| Playwright e2e (`global-setup.ts`, `fixtures.ts`, `auth/rbac.spec.ts`, `seed_e2e.py`) | same header bypass; does **not** drive Keycloak login | **no change** to the bypass path |
| Go middleware unit (`internal/middleware/auth_test.go`) | mints RSA keys + mock JWKS with **Keycloak-shaped** issuer/realm/aud | **update**: mock issuer = Clerk-shaped, assert **`azp`** check instead of `aud`; still self-contained (no live Clerk) |
| *(optional)* real Clerk-login e2e | — | **add** via `@clerk/testing`: `clerkSetup()` in global-setup + `setupClerkTestingToken({page})` per test; needs `CLERK_PUBLISHABLE_KEY`/`CLERK_SECRET_KEY` (CI secret). **Decision D6.** |

Recommendation: keep the existing bypass-based suites unchanged (cheap, fast, already green); update only the Go unit mock; add `@clerk/testing` coverage only if the human wants the real Clerk sign-in UI exercised (D6). **R6:** add a Go test job to CI so the `api-go` auth change is actually gated.

---

## 9. Decisions for the human (must answer before T3)

| # | Decision | SA recommendation |
|---|---|---|
| **D1** | **Target stack:** Go-only, or Go + Python parity? Is Python `services/api` user-API being retired in favor of `api-go`? | **Go + UI mandatory.** Do the cheap Python parity port **unless** you confirm Python user-API is retiring → then **Go-only**. |
| **D2** | **On-prem → cloud IdP (trust boundary).** Keycloak is self-hosted (`auth.redarchlabs.local`); Clerk is a **cloud SaaS** holding user identity + PII. Acceptable for this product's data-residency / air-gap / availability / compliance posture? Vendor lock-in + per-MAU cost accepted? | Surfaced, not decided by SA. The directive says "use Clerk," but **own this knowingly** — for an enterprise/on-prem KM, an external hard dependency for login is a real architectural + compliance shift. If on-prem is a requirement, Clerk may be the wrong fit. |
| **D3** | **Existing users:** preserve memberships or greenfield? Column rename `keycloak_sub`→`auth_subject`? | **Email-relink (B)** + rename to `auth_subject`, **if** there are real users to preserve; **Greenfield (A)** if the current user set is dev-only. |
| **D4** | **Cutover style:** hard cutover vs **dual-verify coexistence** (backend accepts Keycloak *or* Clerk by `iss` during a soak window; instant rollback by flipping the UI). | **Dual-verify coexistence window**, then remove Keycloak (Phase 6). Safer rollback. |
| **D5** | **Sign-up UX change:** registration moves from Keycloak-hosted to in-app Clerk `<SignUp/>` (or Clerk-hosted). New sign-in screen branding. *User-facing.* | Approve Clerk's components for speed; confirm allowed sign-in methods (email/password, OAuth, magic link) in the Clerk dashboard. |
| **D6** | **Org model:** keep our own `orgs`/RBAC (Clerk = identity only) vs adopt **Clerk Organizations/B2B**. | **Keep our own orgs + `access_mask`.** Clerk supplies *identity only*; do **not** entangle RBAC with Clerk orgs. (Adopting Clerk B2B is a separate, larger initiative — out of scope.) |
| **D7** | **E2E depth:** keep bypass-only e2e, or add real `@clerk/testing` sign-in coverage? | Keep bypass suites; add `@clerk/testing` only if you want the real login UI exercised. |

---

## 10. Phased implementation plan (for the PE, post-approval)

> Sequencing assumes **D4 = dual-verify coexistence**. Each phase is independently shippable behind config.

- **Phase 0 — Provision (ops/human):** create Clerk instance; keys; configure sign-in methods + JWT template (email/username, §7); decide org model (D6); resolve D1–D7.
- **Phase 1 — Backend verifier (Go):** add Clerk verify path in `api-go` **alongside** Keycloak, selected by `iss` (or `AUTH_PROVIDER` flag). Config + env. Update `auth_test.go`. *(Python parity in lockstep if D1≠retire.)*
- **Phase 2 — UI swap:** `@clerk/nextjs` provider + `middleware.ts` + sign-in/up + token attach (axios + streaming) + logout; point at Clerk dev instance. Backend already accepts Clerk tokens (Phase 1).
- **Phase 3 — User remap (D3):** migration to rename column + email-relink-on-first-login (or greenfield). Backfill/relink path tested against seeded users.
- **Phase 4 — Soak (D4):** run dual-verify in a real environment; monitor 401 rates, relink logs, token-refresh behavior.
- **Phase 5 — Tests/CI:** finalize Go unit mock; optional `@clerk/testing` e2e (D7); **add Go CI job (R6)**; Clerk test keys as CI secrets.
- **Phase 6 — Remove Keycloak:** delete the Keycloak verify path, `keycloak-js`, `KEYCLOAK_*` env, the `keycloak` compose service. Final `.env.example`/README/compose cleanup.

---

## 11. Risks & mitigations

| # | Risk | Mitigation |
|---|---|---|
| **R1** | Keycloak `sub` ≠ Clerk `sub` → orphaned memberships | §6 remap (email-relink/greenfield/import); test against `seed_e2e.py` users before cutover |
| **R2** | Missing/incorrect **`azp`** check → token-origin confusion (security) **or** all-401 | Mandatory `CLERK_ALLOWED_AZP` allowlist; assert in Go unit test; security-analyst review of the verify path |
| **R3** | Cross-origin token attach in non-React axios module | Specified getter (`window.Clerk?.session?.getToken()`); verify Bearer reaches `:8000`; CORS already allows `:3000` |
| **R4** | **External SaaS hard dependency for login** (was self-hosted) — availability, residency, lock-in, cost | Decision D2 (human-owned); document SLA/data-processing terms; dual-verify (D4) enables fast rollback to Keycloak |
| **R5** | Default Clerk token lacks `email`/`username`; ~60s lifetime | §7 JWT template or Backend-API fetch; rely on SDK auto-refresh |
| **R6** | **No Go CI job** → `api-go` auth change unguarded | Add Go test job in Phase 5; run `make go-test` locally on the auth change |
| **R7** | Self-managed migrations diverge (Python Alembic vs Go `golang-migrate`) | Author the column/remap migration in **both** migration systems; verify on a clean DB in both stacks |

## 12. Rollback

Dual-verify (D4) makes rollback a **config flip**: point the UI back at Keycloak (`keycloak-js` retained until Phase 6) and the backend still verifies Keycloak `iss`. No data loss — `access_mask` and memberships are untouched; the only new state is the `auth_subject` remap, which is additive. After Phase 6 (Keycloak removed), rollback requires restoring the Keycloak verify path + service — so **do not start Phase 6 until the soak (Phase 4) is clean.**

## 13. Out of scope

- Replacing our `orgs`/`access_mask` RBAC with Clerk Organizations/B2B (D6 = keep ours).
- Changing service-to-service auth (`BRAIN_API_KEY`/`INTERNAL_API_KEY`) — unchanged.
- Implementing the missing `api-go` internal status-callback handler (pre-existing gap).
- Enterprise SSO/SAML/SCIM, MFA policy design (Clerk dashboard config, not code).
- Any `access_mask` bit-layout / dimension-model change.
- Billing/MAU procurement (ops).

---

## 14. RATIFICATION + per-slice acceptance criteria (BUILD CONTRACT)

**Approved 2026-06-24** (human via TPM). Ratified §9 decisions — build to these values:

| # | Ratified value |
|---|---|
| D1 | **Go `api-go` + UI canonical/mandatory; ALSO do the Python `services/api` parity port** (Python not confirmed retiring → keep CI green). Drop to Go-only only if the human later confirms Python user-API retirement. |
| D2 | **Approved.** Clerk cloud SaaS is acceptable for this product; proceed. |
| D3 | **Preserve existing users** via **verified-email relink on first Clerk login** + rename column `keycloak_sub` → `auth_subject` (safe superset; if no real users it simply never matches → they re-register, harmless). |
| D4 | **Dual-verify coexistence** (backend accepts Keycloak **or** Clerk by `iss`), then remove Keycloak in Phase 6. Config-flip rollback. |
| D5 | **Approve** in-app Clerk `<SignIn/>`/`<SignUp/>`. Default sign-in methods = **email+password + Google OAuth** (Clerk-dashboard config, Phase 0; adjustable later, not code-blocking). |
| D6 | **Keep our own `orgs` + `access_mask`; Clerk = identity ONLY.** Do **not** adopt Clerk B2B orgs. |
| D7 | **Keep** the existing `X-Test-User` bypass e2e/unit suites; add `@clerk/testing` real-login e2e **only if later requested**. |

**MANDATORY GUARDRAILS (non-negotiable in the build):**
- **G-AZP (R2):** `CLERK_ALLOWED_AZP` allowlist is **required**; the verify path **must** check `azp` and **must** get a **security-analyst review** before merge. A verify path that does not enforce `azp` fails review.
- **G-CI (R6):** add a **Go CI test job** (`.github/workflows/ci.yml`) running `make go-test` so the `api-go` auth change is actually gated. The auth slice is not "done" until the Go job is green.

**Phase 0 is a human/ops dependency (blocks live testing/cutover, not code):** human provisions the Clerk instance, provides `CLERK_SECRET_KEY` + `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, configures the **JWT template** to include `email` + `username` (§7), and sets sign-in methods (D5). Code slices below can be built against a dev Clerk instance once keys exist.

### Global / done-for-the-whole-migration
- **AC-G1** No `KEYCLOAK_*` / `NEXT_PUBLIC_KEYCLOAK_*` / `keycloak-js` reference remains after Phase 6 (grep-clean); `BRAIN_API_KEY`, `INTERNAL_API_KEY`, `API_SECRET_KEY`, `API_E2E_TEST_MODE`/`E2E_TEST_SECRET`, `NEXT_PUBLIC_API_URL` are unchanged in value and behavior.
- **AC-G2** Service-to-service auth (`X-API-Key`, `X-Internal-API-Key`) is byte-for-byte unchanged; brain-api/worker have **zero** auth diff.
- **AC-G3** `access_mask`/`accessmask` packages and the `user_org_memberships`→dimension→mask computation are **unchanged** (no diff in `permission_config.py` / `permissions.go` / mask packages).
- **AC-G4** `.env.example` documents the new `CLERK_*` vars and removes `KEYCLOAK_*` (Phase 6); both compose files updated consistently.

### Slice 1 — Go `api-go` Clerk verifier (dual-verify) — Phase 1
- **AC-1.1** With a valid **Clerk** RS256 token whose `iss` matches the configured Clerk issuer, `/api/*` requests authenticate; `sub`, `email`, `username` are extracted (via JWT template/claims) and used for provisioning.
- **AC-1.2** With a valid **Keycloak** token (old `iss`), requests still authenticate during the coexistence window (dual-verify by `iss` / `AUTH_PROVIDER`).
- **AC-1.3 (G-AZP)** A Clerk token whose `azp` is **not** in `CLERK_ALLOWED_AZP` is **rejected 401**; a token with no/expired/`nbf`-future or bad-signature is rejected. Negative tests exist for each.
- **AC-1.4** JWKS is fetched from `{iss}/.well-known/jwks.json` (or networkless via `CLERK_JWT_KEY`) and cached; a JWKS-endpoint outage does not crash the server (graceful 401/503, logged).
- **AC-1.5** `internal/config/config.go` exposes `ClerkIssuer/ClerkAllowedAZP/ClerkSecretKey` (+ retains Keycloak fields during coexistence); prod-mandatory validation covers the active provider(s).
- **AC-1.6** Mask-based folder/document filtering behavior is identical pre/post change (existing handler tests still pass).

### Slice 2 — Python `services/api` parity (D1=keep) — Phase 1b
- **AC-2.1** `auth/clerk.py` verifies a Clerk token (RS256, JWKS, **`azp` check**, exp/nbf); `get_current_user` returns the same `CurrentUser` shape; dual-verify by `iss` mirrors Go.
- **AC-2.2 (regression-critical)** The **E2E bypass block** (`X-Test-User`/`X-Test-Secret`, gated by `API_E2E_TEST_MODE`) is **unchanged** — existing pytest + Playwright suites pass without modification.
- **AC-2.3** `provision_user_from_claims` is unchanged except claim source; `CLERK_*` settings added to `config.py`; `KEYCLOAK_*` retained until Phase 6.
- **AC-2.4** `mypy` + `ruff` clean; existing Python test suite green in CI.

### Slice 3 — UI `@clerk/nextjs` — Phase 2
- **AC-3.1** `<ClerkProvider>` wraps the app (replaces `AuthProvider`); `OrgProvider` still nested and functional.
- **AC-3.2** New `ui/src/middleware.ts` (`clerkMiddleware()` + matcher) protects authenticated routes; unauthenticated access redirects to Clerk sign-in.
- **AC-3.3** `<SignIn/>`/`<SignUp/>` render (email+password + Google per D5); the old redirect-stub login page is replaced.
- **AC-3.4 (cross-origin, R3)** API calls to `NEXT_PUBLIC_API_URL` (`:3000`→`:8000`) attach `Authorization: Bearer <Clerk getToken()>` in **both** the axios interceptor (`client.ts`) and the streaming `fetch` (`search.ts`); `X-Org-ID` still attached. A real request reaches `api-go` and authenticates end-to-end.
- **AC-3.5** Logout uses `useClerk().signOut()` (clears `redarch:currentOrgId` first); 401 interceptor targets Clerk sign-in.
- **AC-3.6** `keycloak-js` removed from `ui/package.json` (Phase 6); `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` wired; `bun run type-check` green.

### Slice 4 — User remap migration (D3) — Phase 3
- **AC-4.1** Forward migration renames `user_profiles.keycloak_sub` → `auth_subject` (unique-not-null preserved) in **both** Alembic (Python) and `golang-migrate` (Go) — verified on a clean DB in each stack (R7).
- **AC-4.2** On first Clerk login with no `auth_subject` match, if the Clerk email is **verified** and matches an existing `user_profiles.email`, the row's `auth_subject` is rebound to the Clerk `sub` (one-time, logged); memberships/masks preserved.
- **AC-4.3** Relink only fires for **verified** Clerk emails (no rebind on unverified email — anti-takeover); covered by a negative test.
- **AC-4.4** A brand-new Clerk user (no email match) provisions a fresh `user_profiles` row with `is_site_admin=false` and no membership (unchanged first-login semantics).

### Slice 5 — Tests + CI — Phase 5
- **AC-5.1** `internal/middleware/auth_test.go` updated: mock JWKS uses a **Clerk-shaped issuer**, asserts **`azp` acceptance/rejection** (not `aud`); remains self-contained (no live Clerk). Keycloak-path test retained while coexistence lives.
- **AC-5.2 (G-CI)** A **Go test job** is added to `.github/workflows/ci.yml` (runs `make go-test`, race+cover) and is **green**.
- **AC-5.3** Python + Playwright suites pass unchanged (bypass path). `@clerk/testing` e2e only if D7 later flips.
- **AC-5.4 (G-AZP)** Security-analyst has reviewed the verify path (Go + Python) and signed off on `azp` enforcement, JWKS handling, and issuer pinning.

### Slice 6 — Remove Keycloak — Phase 6 (only after Phase 4 soak is clean)
- **AC-6.1** Keycloak verify path, `AUTH_PROVIDER` dual-branch, `keycloak-js`, all `KEYCLOAK_*`/`NEXT_PUBLIC_KEYCLOAK_*` env, and the `keycloak` compose service are removed (satisfies AC-G1).
- **AC-6.2** Full stack (Go + UI, and Python if kept) authenticates **only** via Clerk; all CI jobs green; rollback note updated (post-Phase-6 rollback requires restoring the Keycloak path — documented).

> **SA follow-through:** I will review the PE's diff per slice (peer review), confirming in-scope and that G-AZP/G-CI are met; security-analyst owns the verify-path sign-off (AC-5.4). The spec doc stays uncommitted until the PE commits it with Slice 1.
