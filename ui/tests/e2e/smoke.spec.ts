import { expect, test } from "@playwright/test";

/**
 * Smoke tests — verify the app boots and redirects unauthenticated
 * visitors to Keycloak. Fuller journeys (upload, chat, RLS) require
 * a seeded test environment and run in CI against a staging deployment.
 */

test.describe("app boot", () => {
  test("root redirects to login", async ({ page }) => {
    await page.goto("/");
    // Either already at /login, or Keycloak redirect URL kicks in.
    // Accept any URL that includes "login" (Keycloak realm URL contains it too).
    await page.waitForLoadState("domcontentloaded");
    const url = page.url();
    expect(url).toMatch(/login|keycloak|auth/i);
  });

  test("login page renders", async ({ page }) => {
    // Short-circuit the Keycloak redirect by going to the internal login route.
    // In a real environment, Keycloak intercepts this; this test only
    // validates our route handler renders.
    await page.goto("/login");
    await expect(page).toHaveTitle(/Red Arch/i);
  });
});
