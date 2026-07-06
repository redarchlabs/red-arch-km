import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";

/**
 * Real route protection (an improvement over the old client-only gate): every
 * route except the sign-in / sign-up pages requires an authenticated Clerk
 * session. `auth.protect()` redirects unauthenticated users to the configured
 * sign-in URL (/login).
 */
// `/intake/*` is the public, unauthenticated intake-form page: an external user
// (holding only a form-link token) fills it in without a Clerk session.
const isPublicRoute = createRouteMatcher(["/login(.*)", "/sign-up(.*)", "/intake(.*)"]);

export default clerkMiddleware(async (auth, req) => {
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
