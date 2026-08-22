import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";
import { NextResponse } from "next/server";

import { isBypassEnabled } from "@/lib/auth/bypass";

/**
 * Real route protection (an improvement over the old client-only gate): every
 * route except the sign-in / sign-up pages requires an authenticated Clerk
 * session. `auth.protect()` redirects unauthenticated users to the configured
 * sign-in URL (/login).
 */
// Public, unauthenticated surface:
// - `/` and `/help/*` are the marketing/informational pages a logged-out
//   visitor sees before signing in (the root page redirects signed-in users
//   to /documents itself).
// - `/intake/*` is the public intake-form page: an external user (holding only
//   a form-link token) fills it in without a Clerk session.
// - `/s/*` is a SHARED VIEW: a view an org admin has explicitly opened to
//   anonymous access (a crew station on a tablet, a status board). The token in
//   the path is the only credential, and what it permits is enforced by the API
//   (`api/services/view_share.py`) — the record is pinned and only the view's own
//   workflows can run. Every view is closed here until someone opts it in.
const isPublicRoute = createRouteMatcher([
  "/",
  "/help(.*)",
  "/login(.*)",
  "/sign-up(.*)",
  "/intake(.*)",
  "/s(.*)",
]);

/**
 * Route protection.
 *
 * Chosen once at module load. Offline bypass skips `clerkMiddleware` entirely rather than
 * making every route public within it: `auth.protect()` needs to reach Clerk server-side,
 * and with no internet that stalls the request instead of failing it. Client-side guarding
 * still applies — the authenticated layout redirects on `!isAuthenticated` from useAuth(),
 * and the API is the real enforcement point either way.
 */
export default isBypassEnabled()
  ? function bypassMiddleware() {
      return NextResponse.next();
    }
  : clerkMiddleware(async (auth, req) => {
      if (!isPublicRoute(req)) {
        await auth.protect();
      }
    });

export const config = {
  matcher: [
    // Skip Next.js internals and static files, unless found in search params.
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    // Always run for API routes.
    "/(api|trpc)(.*)",
  ],
};
