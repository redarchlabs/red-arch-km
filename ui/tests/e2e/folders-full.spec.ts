import { expect, test } from "./fixtures";
import { FoldersPage } from "./pages/folders.page";

/**
 * Folder Management flow tests.
 * Covers folder creation, nesting, and document organization.
 *
 * Requires E2E_WITH_BACKEND=1 for full integration with API.
 */

test.describe("Folder Management Flow", () => {
  test.skip(!process.env.E2E_WITH_BACKEND, "requires seeded backend");

  let parentFolderId: string;
  let childFolderId: string;
  let documentId: string;
  const parentFolderName = `parent-${Date.now()}`;
  const childFolderName = `child-${Date.now()}`;

  test("create parent folder via API", async ({ apiContext }) => {
    const res = await apiContext.post("/api/folders/", {
      data: { name: parentFolderName, description: "Parent folder for E2E test" },
    });

    expect(res.status()).toBe(201);
    const folder = (await res.json()) as { id: string; name: string; dot_path: string };
    parentFolderId = folder.id;
    expect(folder.name).toBe(parentFolderName);
    expect(folder.dot_path).toBe(parentFolderName);
  });

  test("create child folder and verify dot_path", async ({ apiContext }) => {
    const res = await apiContext.post("/api/folders/", {
      data: { name: childFolderName, description: "Child folder for E2E test" },
    });

    expect(res.status()).toBe(201);
    const folder = (await res.json()) as { id: string; name: string; dot_path: string };
    childFolderId = folder.id;
    expect(folder.dot_path).toBe(childFolderName);
  });

  test("move folder under parent and verify dot_path updates", async ({ apiContext }) => {
    const res = await apiContext.patch(`/api/folders/${childFolderId}`, {
      data: { parent_id: parentFolderId },
    });

    expect(res.ok()).toBe(true);
    const folder = (await res.json()) as { dot_path: string };
    expect(folder.dot_path).toBe(`${parentFolderName}.${childFolderName}`);
  });

  test("create document in child folder", async ({ apiContext }) => {
    const docTitle = `nested-doc-${Date.now()}`;
    const res = await apiContext.post("/api/documents/", {
      data: {
        title: docTitle,
        description: "Document in nested folder",
        text: "This document is in a nested folder.",
        folder_id: childFolderId,
      },
    });

    expect(res.status()).toBe(201);
    const doc = (await res.json()) as { id: string };
    documentId = doc.id;
  });

  test("list folders via UI", async ({ page, e2eState }) => {
    const foldersPage = new FoldersPage(page);

    // Inject test session headers
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

    await foldersPage.goto();
    await foldersPage.waitForFoldersHeading();
    await foldersPage.expectFolderVisible(parentFolderName);
    await foldersPage.expectFolderVisible(childFolderName);
  });

  test("verify nested folder structure via API", async ({ apiContext }) => {
    const res = await apiContext.get("/api/folders/");
    expect(res.ok()).toBe(true);

    const list = (await res.json()) as {
      items: Array<{ id: string; name: string; dot_path: string }>;
    };
    const parent = list.items.find((f) => f.id === parentFolderId);
    const child = list.items.find((f) => f.id === childFolderId);

    expect(parent?.dot_path).toBe(parentFolderName);
    expect(child?.dot_path).toBe(`${parentFolderName}.${childFolderName}`);
  });

  test("move document to different folder", async ({ apiContext }) => {
    const newFolderRes = await apiContext.post("/api/folders/", {
      data: { name: `folder-${Date.now()}` },
    });
    const newFolder = (await newFolderRes.json()) as { id: string };

    const res = await apiContext.patch(`/api/documents/${documentId}`, {
      data: { folder_id: newFolder.id },
    });

    expect(res.ok()).toBe(true);
    const doc = (await res.json()) as { folder_id: string };
    expect(doc.folder_id).toBe(newFolder.id);
  });

  test("cannot create folder with missing name", async ({ apiContext }) => {
    const res = await apiContext.post("/api/folders/", {
      data: { description: "Missing name" },
    });

    expect([400, 422]).toContain(res.status());
  });

  test("cannot create folder with empty name", async ({ apiContext }) => {
    const res = await apiContext.post("/api/folders/", {
      data: { name: "", description: "Empty name" },
    });

    expect([400, 422]).toContain(res.status());
  });

  test("access non-existent folder returns 404", async ({ apiContext }) => {
    const fakeId = `nonexistent-${Date.now()}`;
    const res = await apiContext.get(`/api/folders/${fakeId}`);

    expect(res.status()).toBe(404);
  });

  test("cannot reparent folder under itself (cycle prevention)", async ({ apiContext }) => {
    // Try to set a folder's parent to itself
    const res = await apiContext.patch(`/api/folders/${parentFolderId}`, {
      data: { parent_id: parentFolderId },
    });

    // Should reject as a cycle
    expect([400, 422, 409]).toContain(res.status());
  });

  test("cannot reparent folder under its own descendant (cycle prevention)", async ({ apiContext }) => {
    // Try to set parent's parent to child (would create a cycle)
    const res = await apiContext.patch(`/api/folders/${parentFolderId}`, {
      data: { parent_id: childFolderId },
    });

    // Should reject as a cycle
    expect([400, 422, 409]).toContain(res.status());
  });

  test("cannot reparent to non-existent parent folder", async ({ apiContext }) => {
    const fakeParentId = `nonexistent-${Date.now()}`;
    const res = await apiContext.patch(`/api/folders/${parentFolderId}`, {
      data: { parent_id: fakeParentId },
    });

    // Should reject (404 or 400)
    expect([400, 404]).toContain(res.status());
  });

  test("clean up: delete folders and documents", async ({ apiContext }) => {
    // Delete document
    await apiContext.delete(`/api/documents/${documentId}`);

    // Delete child folder
    await apiContext.delete(`/api/folders/${childFolderId}`);

    // Delete parent folder
    const res = await apiContext.delete(`/api/folders/${parentFolderId}`);
    expect(res.status()).toBe(204);
  });
});
