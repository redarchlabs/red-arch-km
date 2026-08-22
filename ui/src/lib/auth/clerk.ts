/**
 * Clerk token access for NON-React modules (the axios client and the streaming
 * fetch). React components should use Clerk's `useAuth().getToken()`; modules
 * that run outside React read the global Clerk instance that `<ClerkProvider>`
 * mounts on `window`.
 *
 * API calls are cross-origin (:3002 → :8000), so Clerk's same-origin session
 * cookie is insufficient — we must attach `Authorization: Bearer <getToken()>`.
 * Clerk session tokens are short-lived (~60s) and `getToken()` transparently
 * refreshes them, so there is no manual refresh step.
 */

interface ClerkSession {
  getToken: (options?: { template?: string }) => Promise<string | null>;
}

interface ClerkGlobal {
  session?: ClerkSession | null;
  signOut?: (options?: { redirectUrl?: string }) => Promise<void>;
}

function getClerk(): ClerkGlobal | undefined {
  if (typeof window === "undefined") {
    return undefined;
  }
  return (window as unknown as { Clerk?: ClerkGlobal }).Clerk;
}

/**
 * How long to wait for Clerk to mint a token before giving up. A mint is
 * normally sub-second; this only fires when Clerk's own network call stalls.
 * Must stay well under the axios client's 30s request timeout so the failure
 * is attributable to the token, not the API.
 */
export const TOKEN_TIMEOUT_MS = 10_000;

/** Rejects after `ms`; the returned canceller clears the pending timer. */
function timeoutAfter(ms: number): { promise: Promise<never>; cancel: () => void } {
  let timer: ReturnType<typeof setTimeout>;
  const promise = new Promise<never>((_resolve, reject) => {
    timer = setTimeout(
      () => reject(new Error("Timed out waiting for a sign-in token from Clerk.")),
      ms,
    );
  });
  return { promise, cancel: () => clearTimeout(timer) };
}

/**
 * Returns a fresh Clerk session JWT, or null when unauthenticated / before
 * Clerk has loaded, or when the mint fails outright (callers then proceed
 * unauthenticated and the API 401 + response interceptor drive the sign-in
 * redirect).
 *
 * THROWS in exactly one case: the mint never settles within
 * {@link TOKEN_TIMEOUT_MS}. Callers await this inside the axios request
 * interceptor, so a hung mint means the request is never dispatched and
 * axios's own timeout never starts — the caller's promise would hang forever
 * and the UI would sit on a skeleton that looks like empty data. Rejecting
 * turns that into a visible, retryable error. Returning null instead would
 * send the request unauthenticated and bounce a still-valid session to /login.
 */
export async function getToken(): Promise<string | null> {
  const session = getClerk()?.session;
  if (!session) {
    return null;
  }
  // Mint via the configured JWT template so the token carries email/username
  // — Clerk's DEFAULT session token does NOT include them, but the api-go/api
  // verifiers extract email/username for provisioning. The template name MUST
  // match a template configured in the Clerk dashboard (Phase 0). When unset,
  // the default token is used and the backend provisions with empty
  // email/username.
  const template = process.env.NEXT_PUBLIC_CLERK_JWT_TEMPLATE;
  const mint = (async () => {
    try {
      return await session.getToken(template ? { template } : undefined);
    } catch {
      return null;
    }
  })();

  const timeout = timeoutAfter(TOKEN_TIMEOUT_MS);
  try {
    return await Promise.race([mint, timeout.promise]);
  } finally {
    timeout.cancel();
  }
}

/**
 * Clears persisted per-user state then signs out via Clerk, returning the user
 * to the sign-in page. Safe to call from non-React modules.
 */
export function logout(): void {
  if (typeof window !== "undefined") {
    localStorage.removeItem("redarch:currentOrgId");
  }
  void getClerk()?.signOut?.({ redirectUrl: "/login" });
}
