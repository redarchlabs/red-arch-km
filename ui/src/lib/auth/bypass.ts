/**
 * OFFLINE BYPASS AUTHENTICATION — disables Clerk and authenticates as a site admin.
 *
 * Why this exists: the robot demo runs on an isolated network with NO internet, and the
 * UI can only sign people in through Clerk. Offline, Clerk's JS never loads, `getToken()`
 * returns null, every request goes out unauthenticated and the 401 interceptor bounces to
 * /login forever — so the operator console, which is where the robot is driven from, is
 * unreachable. The API already accepts `X-Test-User` + `X-Test-Secret` in place of a Clerk
 * JWT when `API_E2E_TEST_MODE` is on (see api/auth/dependencies.py::_resolve_e2e_user);
 * this is the browser half of that door.
 *
 * OFF unless `NEXT_PUBLIC_BYPASS_AUTH=1` is set at BUILD time. With it unset every code
 * path here is inert and the Clerk behaviour is unchanged.
 *
 * THE SECRET IS DELIBERATELY NOT AN ENV VAR. A `NEXT_PUBLIC_*` secret is inlined into the
 * JavaScript bundle, and that bundle is served to *every* browser that loads the app —
 * including the iPad opening a Crew Station through an anonymous /s/<token> link, which is
 * handed to visitors. There is one bundle, so it cannot be given to the console and
 * withheld from the kiosk: a visitor opening devtools would hold site-admin API
 * credentials. Instead the operator types the secret once, it lives in `sessionStorage`
 * for that tab alone, and visitor devices never receive it.
 *
 * Consequences worth knowing:
 *   - closing the tab ends the session (sessionStorage, not localStorage — deliberate).
 *   - a build that ships with the flag on merely shows a login form; it does not hand out
 *     access on its own.
 */

/** sessionStorage key holding the operator-entered shared secret. */
const SECRET_KEY = "redarch:bypassSecret";

/**
 * The identity bypass mode authenticates as. `e2e-siteadmin` already exists with
 * `is_site_admin = true` and org-admin membership in every org, so no seeding is needed.
 *
 * Do NOT change this to a fresh name: `provision_user_from_claims` matches strictly on
 * `auth_subject` (= `e2e-<username>`), so an unrecognised name silently creates a
 * member-of-nothing user and the org switcher reads "No organizations".
 */
export const BYPASS_USER = "siteadmin";

/**
 * Whether offline bypass auth is compiled in.
 *
 * Read from `process.env` rather than cached in a module constant so tests can flip it
 * with `vi.stubEnv`. Next inlines `NEXT_PUBLIC_*` in browser bundles and reads it at
 * runtime on the server, so one variable covers the browser, middleware and server
 * components alike.
 */
export function isBypassEnabled(): boolean {
  return process.env.NEXT_PUBLIC_BYPASS_AUTH === "1";
}

/**
 * The stored secret, or null when none has been entered (or storage is unavailable).
 *
 * Every access is guarded: `sessionStorage` THROWS rather than returning null in a
 * private window or with site data blocked, and an exception here would take down the
 * auth facade that every page depends on.
 */
export function getBypassSecret(): string | null {
  if (typeof window === "undefined") {
    return null;
  }
  try {
    return window.sessionStorage.getItem(SECRET_KEY) || null;
  } catch {
    return null;
  }
}

/** Persist the operator-entered secret for this tab. Returns false if storage refused. */
export function setBypassSecret(secret: string): boolean {
  if (typeof window === "undefined") {
    return false;
  }
  try {
    window.sessionStorage.setItem(SECRET_KEY, secret);
    return true;
  } catch {
    return false;
  }
}

/** Forget the secret — this is what "sign out" means in bypass mode. */
export function clearBypassSecret(): void {
  if (typeof window === "undefined") {
    return;
  }
  try {
    window.sessionStorage.removeItem(SECRET_KEY);
  } catch {
    // Nothing to do: an unreadable store is already an unauthenticated one.
  }
}

/**
 * Auth headers for the API's test-user door, or `{}` when no secret is stored.
 *
 * Returning empty rather than throwing is intentional: the request then goes out
 * unauthenticated, the API 401s, and the existing response interceptor redirects to
 * /login — which in bypass mode is the form that asks for the secret. The failure lands
 * the operator exactly where they can fix it.
 */
export function bypassHeaders(): Record<string, string> {
  const secret = getBypassSecret();
  if (!secret) {
    return {};
  }
  return { "X-Test-User": BYPASS_USER, "X-Test-Secret": secret };
}
