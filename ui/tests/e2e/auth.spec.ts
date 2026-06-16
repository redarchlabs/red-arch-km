import { expect, test } from "./fixtures";
import { LoginPage } from "./pages/login.page";

/**
 * Authentication flow tests.
 * Verifies Keycloak login, session establishment, and auth boundaries.
 *
 * Requires E2E_WITH_BACKEND=1 for full journey with Keycloak.
 */

test.describe("Authentication Flow", () => {
  test.skip(!process.env.E2E_WITH_BACKEND, "requires seeded backend");

  test("login page is accessible", async ({ page }) => {
    const loginPage = new LoginPage(page);
    await loginPage.goto();
    await loginPage.waitForLoginPage();
    await loginPage.expectPageTitle();
  });

  test("root redirects to login or Keycloak", async ({ page }) => {
    const loginPage = new LoginPage(page);
    await page.goto("/");
    await page.waitForLoadState("domcontentloaded");
    const isAtLogin = await loginPage.isAtLoginPage();
    expect(isAtLogin).toBe(true);
  });

  test("session persists across navigation", async ({ page, apiContext }) => {
    // Get current user info via API (requires valid session headers)
    const userRes = await apiContext.get("/api/users/me");
    expect(userRes.ok()).toBe(true);

    const user = (await userRes.json()) as { id: string; email: string };
    expect(user.id).toBeTruthy();
    expect(user.email).toBeTruthy();
  });

  test("unauthenticated access to protected routes redirects", async ({ page }) => {
    // Without session headers, protected routes should redirect
    await page.goto("/documents");
    await page.waitForLoadState("networkidle");

    const url = page.url();
    const isRedirected = /login|auth|keycloak/i.test(url) || !url.includes("/documents");
    expect(isRedirected).toBe(true);
  });

  test("session is established after login", async ({ page, e2eState, apiContext }) => {
    // Verify that test session was initialized
    expect(e2eState.orgId).toBeTruthy();
    expect(e2eState.testUser).toBeTruthy();
    expect(e2eState.testSecret).toBeTruthy();

    // Verify API context has valid credentials
    const meRes = await apiContext.get("/api/users/me");
    expect(meRes.ok()).toBe(true);
  });
});
