import { expect, test } from "@playwright/test";

/**
 * Smoke tests — verify the app boots and redirects unauthenticated visitors to
 * the sign-in page. Auth is now enforced by Clerk (replacing the old Keycloak
 * gate). Fuller journeys (upload, chat, RLS) require a seeded test environment
 * and run in CI against a staging deployment.
 */

test.describe("app boot", () => {
  test("root redirects to login when unauthenticated", async ({ page }) => {
    await page.goto("/");
    // Auth-gating runs in two layers: clerkMiddleware on the server
    // (src/middleware.ts — auth.protect() on every route except /login and
    // /sign-up) and the client-side (authenticated)/layout gate
    // (router.replace("/login") when !isAuthenticated). The root path first
    // performs its app-level redirect to /documents, then the auth gate bounces
    // the unauthenticated visitor to /login. That client redirect fires AFTER
    // Clerk initializes, so we must wait for the URL to SETTLE — sampling it at
    // "domcontentloaded" catches the transient /documents state mid-hydration.
    await page.waitForURL(/login|clerk|accounts|auth/i, { timeout: 15_000 });
    expect(page.url()).toMatch(/login|clerk|accounts|auth/i);
  });

  test("login page renders", async ({ page }) => {
    // /login is a public route (Clerk mounts the embedded <SignIn> here). This
    // test only validates that our route handler renders.
    await page.goto("/login");
    await expect(page).toHaveTitle(/Red Arch/i);
  });
});
