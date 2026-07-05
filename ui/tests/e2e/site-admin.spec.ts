import { request, type APIRequestContext } from "@playwright/test";

import { expect, test } from "./fixtures";
import { SiteAdminPage } from "./pages/site-admin.page";

/**
 * Global site-admin console: /api/admin endpoints + /site-admin UI.
 *
 * Runs as the seeded `e2e_admin` (is_site_admin=true). A throwaway regular
 * user is auto-provisioned via the X-Test-User header to exercise promotion,
 * deactivation, and the non-admin 403 path.
 *
 * Requires E2E_WITH_BACKEND=1 and a seeded backend (see journey.spec.ts).
 */

test.describe("site-admin console", () => {
  test.skip(!process.env.E2E_WITH_BACKEND, "requires seeded backend");

  async function contextFor(
    user: string,
    e2eState: { apiUrl: string; testSecret: string; orgId: string },
  ): Promise<APIRequestContext> {
    return request.newContext({
      baseURL: e2eState.apiUrl,
      extraHTTPHeaders: {
        "X-Test-User": user,
        "X-Test-Secret": e2eState.testSecret,
        "X-Org-ID": e2eState.orgId,
      },
    });
  }

  test("setup status reports initialized on a seeded instance", async ({ apiContext }) => {
    const res = await apiContext.get("/api/setup/status");
    expect(res.status()).toBe(200);
    expect(await res.json()).toEqual({ needs_setup: false });
  });

  test("setup claim is refused once an admin exists", async ({ apiContext }) => {
    const res = await apiContext.post("/api/setup/claim", {
      data: { token: "bogus-token" },
    });
    expect(res.status()).toBe(409);
  });

  test("admin can list, search, promote, demote, and deactivate users", async ({
    apiContext,
    e2eState,
  }) => {
    const username = `sa-target-${Date.now()}`;
    // Provision the target by making any authenticated call as them.
    const targetCtx = await contextFor(`${username}:${username}@e2e.local`, e2eState);
    expect((await targetCtx.get("/api/users/me")).status()).toBe(200);

    const listed = await apiContext.get(`/api/admin/users?q=${username}`);
    expect(listed.status()).toBe(200);
    const page = await listed.json();
    expect(page.total).toBe(1);
    const target = page.items[0];
    expect(target.is_site_admin).toBe(false);
    expect(target.is_active).toBe(true);

    // Promote → demote → deactivate → reactivate round trip.
    let res = await apiContext.patch(`/api/admin/users/${target.id}`, {
      data: { is_site_admin: true },
    });
    expect(res.status()).toBe(200);
    expect((await res.json()).is_site_admin).toBe(true);

    res = await apiContext.patch(`/api/admin/users/${target.id}`, {
      data: { is_site_admin: false, is_active: false },
    });
    expect(res.status()).toBe(200);
    const demoted = await res.json();
    expect(demoted.is_site_admin).toBe(false);
    expect(demoted.is_active).toBe(false);

    // Deactivated user is rejected at auth time despite valid credentials.
    expect((await targetCtx.get("/api/users/me")).status()).toBe(403);

    res = await apiContext.patch(`/api/admin/users/${target.id}`, {
      data: { is_active: true },
    });
    expect(res.status()).toBe(200);
    expect((await targetCtx.get("/api/users/me")).status()).toBe(200);
    await targetCtx.dispose();
  });

  test("admin cannot demote or deactivate themselves", async ({ apiContext }) => {
    const me = await (await apiContext.get("/api/users/me")).json();
    for (const data of [{ is_site_admin: false }, { is_active: false }]) {
      const res = await apiContext.patch(`/api/admin/users/${me.id}`, { data });
      expect(res.status()).toBe(400);
    }
  });

  test("non-admin gets 403 from every admin endpoint", async ({ e2eState }) => {
    const username = `sa-nobody-${Date.now()}`;
    const ctx = await contextFor(`${username}:${username}@e2e.local`, e2eState);
    expect((await ctx.get("/api/admin/users")).status()).toBe(403);
    expect((await ctx.get("/api/admin/system")).status()).toBe(403);
    const me = await (await ctx.get("/api/users/me")).json();
    expect(
      (await ctx.patch(`/api/admin/users/${me.id}`, { data: { is_site_admin: true } })).status(),
    ).toBe(403);
    await ctx.dispose();
  });

  test("system status reports all components", async ({ apiContext }) => {
    const res = await apiContext.get("/api/admin/system");
    expect(res.status()).toBe(200);
    const body = await res.json();
    expect(Object.keys(body.components).sort()).toEqual([
      "brain_api",
      "database",
      "redis",
      "worker_queue",
    ]);
    expect(body.components.database.status).toBe("ok");
    expect(body.version).toBeTruthy();
  });

  test("admin membership summary lists the user's orgs", async ({ apiContext, e2eState }) => {
    const me = await (await apiContext.get("/api/users/me")).json();
    const res = await apiContext.get(`/api/admin/users/${me.id}/memberships`);
    expect(res.status()).toBe(200);
    const memberships = await res.json();
    expect(Array.isArray(memberships)).toBe(true);
    // e2e_admin is seeded with a membership in the E2E org.
    expect(memberships.some((m: { org_id: string }) => m.org_id === e2eState.orgId)).toBe(true);
  });

  test("console UI renders for the seeded admin", async ({ page, e2eState }) => {
    await page.addInitScript(
      ({ testUser, testSecret, orgId }) => {
        const origFetch = window.fetch;
        window.fetch = (input: RequestInfo | URL, init: RequestInit = {}) => {
          const headers = new Headers(init.headers ?? {});
          headers.set("X-Test-User", testUser);
          headers.set("X-Test-Secret", testSecret);
          headers.set("X-Org-ID", orgId);
          return origFetch(input, { ...init, headers });
        };
      },
      {
        testUser: e2eState.testUser,
        testSecret: e2eState.testSecret,
        orgId: e2eState.orgId,
      },
    );

    const siteAdmin = new SiteAdminPage(page);
    await page.goto(`${e2eState.uiUrl}/site-admin`);
    await siteAdmin.waitForConsole();
    await siteAdmin.expectOrgListed("E2E Test Org");
  });
});
