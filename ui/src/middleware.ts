import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";

/**
 * Real route protection (an improvement over the old client-only gate): every
 * route except the sign-in / sign-up pages requires an authenticated Clerk
 * session. `auth.protect()` redirects unauthenticated users to the configured
 * sign-in URL (/login).
 */
const isPublicRoute = createRouteMatcher(["/login(.*)", "/sign-up(.*)"]);

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
