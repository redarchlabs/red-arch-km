import { expect, test } from "./fixtures";

/**
 * Full journey: seeded test admin creates a folder, uploads a document,
 * and verifies it appears in the documents list. Chat is covered in a
 * separate spec because it hits OpenAI and is more expensive.
 *
 * Requires E2E_WITH_BACKEND=1, E2E_TEST_SECRET, a running API with
 * API_E2E_TEST_MODE=true, and a seeded DB (see services/api/scripts/seed_e2e.py).
 */

test.describe("documents journey", () => {
  test.skip(!process.env.E2E_WITH_BACKEND, "requires seeded backend");

  test("create folder via API and see it in the UI", async ({
    page,
    apiContext,
    e2eState,
  }) => {
    const folderName = `e2e-folder-${Date.now()}`;

    const res = await apiContext.post("/api/folders/", {
      data: { name: folderName, description: "Created by E2E journey test" },
    });
    expect(res.status()).toBe(201);

    // Point the UI at the same session. We bake the X-Test-User headers
    // into the browser context via a small script that hooks fetch.
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

    await page.goto(`${e2eState.uiUrl}/folders`);

    // The Clerk middleware will try to redirect us — the test here verifies
    // the server-side flow works, not the full Clerk sign-in dance.
    // If we see the folder name in the DOM the chain is working.
    await expect(page.getByText(folderName, { exact: false })).toBeVisible({
      timeout: 15_000,
    });
  });

  test("create document via API and see it in the list", async ({
    apiContext,
  }) => {
    const title = `e2e-doc-${Date.now()}`;

    const createRes = await apiContext.post("/api/documents/", {
      data: {
        title,
        description: "E2E test document",
        text: "This document was created by a Playwright E2E test.",
      },
    });
    expect(createRes.status()).toBe(201);

    const listRes = await apiContext.get("/api/documents/");
    expect(listRes.ok()).toBe(true);
    const body = (await listRes.json()) as {
      items: Array<{ title: string }>;
    };
    expect(body.items.some((d) => d.title === title)).toBe(true);
  });

  test("folder PATCH reparents with dot_path rebuild", async ({ apiContext }) => {
    const parentName = `parent-${Date.now()}`;
    const childName = `child-${Date.now()}`;

    const parentRes = await apiContext.post("/api/folders/", {
      data: { name: parentName },
    });
    const parent = (await parentRes.json()) as { id: string; dot_path: string };

    const childRes = await apiContext.post("/api/folders/", {
      data: { name: childName },
    });
    const child = (await childRes.json()) as { id: string; dot_path: string };

    expect(child.dot_path).toBe(childName);

    const patchRes = await apiContext.patch(`/api/folders/${child.id}`, {
      data: { parent_id: parent.id },
    });
    expect(patchRes.ok()).toBe(true);
    const moved = (await patchRes.json()) as { dot_path: string };
    expect(moved.dot_path).toBe(`${parentName}.${childName}`);
  });
});
