import { expect, test } from "./fixtures";

/**
 * Brain API flow tests.
 * Verifies document ingestion into the knowledge base.
 *
 * Requires E2E_WITH_BACKEND=1 and a running Qdrant + Brain API service.
 */

// Serial: these tests form an ordered chain (create -> ingest -> verify ->
// clean up) that shares describe-scoped `documentId`/`documentTitle`. The suite
// runs under `fullyParallel: true`, so without serial mode Playwright would
// schedule these across workers and race on that shared mutable state.
test.describe.serial("Brain API / Knowledge Base Integration", () => {
  test.skip(!process.env.E2E_WITH_BACKEND, "requires seeded backend with Brain API");

  let documentId: string;
  let documentTitle: string;

  test("create document", async ({ apiContext }) => {
    documentTitle = `kb-doc-${Date.now()}`;
    const res = await apiContext.post("/api/documents/", {
      data: {
        title: documentTitle,
        description: "Document for knowledge base ingestion",
        text: "This is a detailed document about machine learning fundamentals. It covers neural networks, deep learning, and practical applications.",
      },
    });

    expect(res.status()).toBe(201);
    const doc = (await res.json()) as { id: string };
    documentId = doc.id;
  });

  test("ingest document into knowledge base", async ({ apiContext }) => {
    // Trigger ingestion via Brain API
    const res = await apiContext.post("/api/brain/ingest", {
      data: { document_id: documentId },
    });

    // Should succeed or be queued
    expect([200, 201, 202]).toContain(res.status());
  });

  test("verify document appears in knowledge base", async ({ apiContext }) => {
    // Give processing time for ingestion
    await new Promise((r) => setTimeout(r, 2000));

    // Query knowledge base
    const res = await apiContext.post("/api/brain/search", {
      data: { query: documentTitle, limit: 10 },
    });

    expect(res.ok()).toBe(true);
    const results = (await res.json()) as {
      results: Array<{ document_id: string; score: number }>;
    };

    const found = results.results.some((r) => r.document_id === documentId);
    expect(found).toBe(true);
  });

  test("search knowledge base returns relevant results", async ({ apiContext }) => {
    const res = await apiContext.post("/api/brain/search", {
      data: { query: "neural networks deep learning", limit: 5 },
    });

    expect(res.ok()).toBe(true);
    const results = (await res.json()) as {
      results: Array<{ score: number }>;
    };

    expect(results.results.length).toBeGreaterThan(0);
    expect(results.results[0].score).toBeGreaterThan(0);
  });

  test("knowledge base rejects invalid queries gracefully", async ({ apiContext }) => {
    const res = await apiContext.post("/api/brain/search", {
      data: { query: "" },
    });

    // Should either return empty results or 400
    expect([200, 400]).toContain(res.status());
  });

  test("clean up: delete ingested document", async ({ apiContext }) => {
    const res = await apiContext.delete(`/api/documents/${documentId}`);
    expect(res.status()).toBe(204);

    // Verify ingestion record is cleaned up (optional, depends on implementation)
    const searchRes = await apiContext.post("/api/brain/search", {
      data: { query: documentTitle, limit: 10 },
    });

    if (searchRes.ok()) {
      const results = (await searchRes.json()) as {
        results: Array<{ document_id: string }>;
      };
      const found = results.results.some((r) => r.document_id === documentId);
      expect(found).toBe(false);
    }
  });
});
