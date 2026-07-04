import { expect, test } from "./fixtures";
import { DocumentsPage } from "./pages/documents.page";

/**
 * Document Management flow tests.
 * Covers CRUD operations: create, read, update, delete documents.
 *
 * Requires E2E_WITH_BACKEND=1 for full integration with API.
 */

// Serial: these tests form an ordered CRUD chain (create -> read -> update ->
// delete) that shares describe-scoped `createdDocId`/`documentTitle`. The suite
// runs under `fullyParallel: true`, so without serial mode Playwright would
// schedule these across workers and race on that shared mutable state.
test.describe.serial("Document Management Flow", () => {
  test.skip(!process.env.E2E_WITH_BACKEND, "requires seeded backend");

  let createdDocId: string;
  let documentTitle: string;

  test("create a document via API", async ({ apiContext }) => {
    documentTitle = `e2e-doc-${Date.now()}`;
    const res = await apiContext.post("/api/documents/", {
      data: {
        title: documentTitle,
        description: "Created by E2E test",
        text: "This is test content for document management.",
      },
    });

    expect(res.status()).toBe(201);
    const doc = (await res.json()) as { id: string; title: string };
    createdDocId = doc.id;
    expect(doc.title).toBe(documentTitle);
  });

  test("read document list and see created document", async ({ apiContext }) => {
    const res = await apiContext.get("/api/documents/");
    expect(res.ok()).toBe(true);

    const list = (await res.json()) as { items: Array<{ id: string; title: string }> };
    const found = list.items.find((d) => d.id === createdDocId);
    expect(found).toBeTruthy();
    expect(found?.title).toBe(documentTitle);
  });

  test("read individual document", async ({ apiContext }) => {
    const res = await apiContext.get(`/api/documents/${createdDocId}`);
    expect(res.ok()).toBe(true);

    const doc = (await res.json()) as { id: string; title: string; text: string };
    expect(doc.id).toBe(createdDocId);
    expect(doc.title).toBe(documentTitle);
    expect(doc.text).toContain("test content");
  });

  test("update document", async ({ apiContext }) => {
    const newTitle = `${documentTitle}-updated`;
    const newText = "Updated content for E2E testing.";

    const res = await apiContext.patch(`/api/documents/${createdDocId}`, {
      data: {
        title: newTitle,
        text: newText,
      },
    });

    expect(res.ok()).toBe(true);
    const doc = (await res.json()) as { title: string; text: string };
    expect(doc.title).toBe(newTitle);
    expect(doc.text).toBe(newText);
  });

  test("list documents via UI", async ({ page, e2eState, apiContext }) => {
    const docsPage = new DocumentsPage(page);

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

    await docsPage.goto();
    await docsPage.waitForDocumentsHeading();
    await docsPage.expectDocumentVisible(documentTitle);
  });

  test("delete document", async ({ apiContext }) => {
    const res = await apiContext.delete(`/api/documents/${createdDocId}`);
    expect(res.status()).toBe(204);

    // Verify document is gone
    const getRes = await apiContext.get(`/api/documents/${createdDocId}`);
    expect(getRes.status()).toBe(404);
  });

  test("document not found after deletion", async ({ apiContext }) => {
    const res = await apiContext.get(`/api/documents/${createdDocId}`);
    expect(res.status()).toBe(404);
  });

  test("cannot create document with missing required fields", async ({ apiContext }) => {
    // Missing title
    const res1 = await apiContext.post("/api/documents/", {
      data: {
        description: "Missing title",
        text: "Some content",
      },
    });
    expect([400, 422]).toContain(res1.status());

    // Missing text
    const res2 = await apiContext.post("/api/documents/", {
      data: {
        title: "Missing content",
      },
    });
    expect([400, 422]).toContain(res2.status());
  });

  test("cannot create document with empty title", async ({ apiContext }) => {
    const res = await apiContext.post("/api/documents/", {
      data: {
        title: "",
        text: "Some content",
      },
    });
    expect([400, 422]).toContain(res.status());
  });

  test("access non-existent document returns 404", async ({ apiContext }) => {
    const fakeId = "nonexistent-" + Date.now();
    const res = await apiContext.get(`/api/documents/${fakeId}`);
    expect(res.status()).toBe(404);
  });

  test("update non-existent document returns 404", async ({ apiContext }) => {
    const fakeId = "nonexistent-" + Date.now();
    const res = await apiContext.patch(`/api/documents/${fakeId}`, {
      data: { title: "Updated" },
    });
    expect(res.status()).toBe(404);
  });

  test("cannot update document with invalid data", async ({ apiContext }) => {
    // Create a document first
    const createRes = await apiContext.post("/api/documents/", {
      data: {
        title: `doc-${Date.now()}`,
        text: "Original content",
      },
    });
    const doc = (await createRes.json()) as { id: string };

    // Try to update with empty title
    const updateRes = await apiContext.patch(`/api/documents/${doc.id}`, {
      data: { title: "" },
    });
    expect([400, 422]).toContain(updateRes.status());

    // Clean up
    await apiContext.delete(`/api/documents/${doc.id}`);
  });
});
