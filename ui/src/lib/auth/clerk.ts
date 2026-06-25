/**
 * Clerk token access for NON-React modules (the axios client and the streaming
 * fetch). React components should use Clerk's `useAuth().getToken()`; modules
 * that run outside React read the global Clerk instance that `<ClerkProvider>`
 * mounts on `window`.
 *
 * API calls are cross-origin (:3000 → :8000), so Clerk's same-origin session
 * cookie is insufficient — we must attach `Authorization: Bearer <getToken()>`.
 * Clerk session tokens are short-lived (~60s) and `getToken()` transparently
 * refreshes them, so there is no manual refresh step.
 */

interface ClerkSession {
  getToken: () => Promise<string | null>;
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
 * Returns a fresh Clerk session JWT, or null when unauthenticated / before
 * Clerk has loaded. Never throws — callers proceed unauthenticated and the API
 * 401 + response interceptor drive the sign-in redirect.
 */
export async function getToken(): Promise<string | null> {
  const session = getClerk()?.session;
  if (!session) {
    return null;
  }
  try {
    return await session.getToken();
  } catch {
    return null;
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
