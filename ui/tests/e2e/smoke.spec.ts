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
    // Discrimination: this test must FAIL when the auth state is wrong, not
    // always-pass. Two guards enforce that:
    //   1. waitForURL fails-closed — if the gate were broken and the visitor was
    //      NOT redirected, it times out and the test fails (rather than the URL
    //      simply not matching at an arbitrary sample point).
    //   2. We explicitly assert we did NOT settle on the protected /documents
    //      route. Without this, a regression that let an unauthenticated visitor
    //      reach app content could still slip past a purely positive check.
    const settledUrl = page.url();
    expect(settledUrl).toMatch(/login|clerk|accounts|auth/i);
    expect(settledUrl).not.toMatch(/\/documents(\b|\/|$)/);
  });

  test("login page renders", async ({ page }) => {
    // /login is a public route (Clerk mounts the embedded <SignIn> here). This
    // test only validates that our route handler renders.
    await page.goto("/login");
    await expect(page).toHaveTitle(/Red Arch/i);
  });
});
