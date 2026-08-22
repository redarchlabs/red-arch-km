/**
 * The single place that decides how an API call proves who it is.
 *
 * Normally that is a Clerk bearer token; with offline bypass compiled in it is the API's
 * test-user header pair instead (see ./bypass.ts for why that mode exists). Both the
 * axios interceptor and every hand-rolled streaming `fetch` go through here, so the two
 * can never drift apart — which matters because the streams are exactly the calls that
 * would silently fall back to unauthenticated and 401 mid-demo.
 */

import { bypassHeaders, isBypassEnabled } from "./bypass";
import { getToken } from "./clerk";

/**
 * Headers that authenticate one API request. Empty when unauthenticated — callers pass it
 * through spread and let the API's 401 drive the sign-in redirect.
 */
export async function authHeaders(): Promise<Record<string, string>> {
  if (isBypassEnabled()) {
    // Synchronous: no token to mint, so this cannot hang the way a stalled Clerk mint can.
    return bypassHeaders();
  }
  const token = await getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}
