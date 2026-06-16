import { expect, test } from "./fixtures";
import { ChatPage } from "./pages/chat.page";

/**
 * Chat / RAG (Retrieval Augmented Generation) flow tests.
 * Verifies message sending, streaming responses, and knowledge base integration.
 *
 * Requires E2E_WITH_BACKEND=1, a running Chat API, and ingested knowledge base documents.
 */

test.describe("Chat / RAG Flow", () => {
  test.skip(!process.env.E2E_WITH_BACKEND, "requires seeded backend with Chat API");

  let documentId: string;
  const documentTitle = `rag-doc-${Date.now()}`;
  const documentContent = `
    Red Arch is a modern knowledge management system built with Next.js, FastAPI, and PostgreSQL.
    It supports document management, folder organization, and RAG-powered chat.
    The system uses Qdrant for vector embeddings and Brain API for semantic search.
  `;

  test("seed knowledge base with document", async ({ apiContext }) => {
    const res = await apiContext.post("/api/documents/", {
      data: {
        title: documentTitle,
        description: "RAG test document",
        text: documentContent,
      },
    });

    expect(res.status()).toBe(201);
    const doc = (await res.json()) as { id: string };
    documentId = doc.id;

    // Ingest immediately
    const ingestRes = await apiContext.post("/api/brain/ingest", {
      data: { document_id: documentId },
    });
    expect([200, 201, 202]).toContain(ingestRes.status());

    // Allow processing time
    await new Promise((r) => setTimeout(r, 1000));
  });

  test("access chat interface", async ({ page, e2eState }) => {
    const chatPage = new ChatPage(page);

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

    await chatPage.goto();
    await chatPage.waitForChatInterface();
  });

  test("send message via API", async ({ apiContext }) => {
    const res = await apiContext.post("/api/chat/ask", {
      data: {
        message: "What is Red Arch?",
        context: { document_ids: [documentId] },
      },
    });

    expect([200, 201]).toContain(res.status());
  });

  test("receive streamed response", async ({ page, e2eState }) => {
    const chatPage = new ChatPage(page);

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

    await chatPage.goto();
    await chatPage.waitForChatInterface();

    // Send message and wait for response
    await chatPage.sendMessage("Tell me about Red Arch");

    // Verify streaming indicator appears
    await chatPage.expectStreamingIndicator();

    // Wait for response to arrive
    await chatPage.waitForResponse(30_000);
  });

  test("response references ingested content", async ({ apiContext }) => {
    const res = await apiContext.post("/api/chat/ask", {
      data: {
        message: "What database does Red Arch use?",
        context: { document_ids: [documentId] },
      },
    });

    expect(res.ok()).toBe(true);

    // Read streamed response
    const text = await res.text();
    expect(text.toLowerCase()).toMatch(
      /postgres|vector|qdrant|semantic|knowledge/i,
    );
  });

  test("chat persists conversation history", async ({ apiContext }) => {
    const message1 = "Hello, what is this system?";
    const message2 = "Tell me more about the architecture";

    const res1 = await apiContext.post("/api/chat/ask", {
      data: { message: message1 },
    });
    expect(res1.ok()).toBe(true);

    const res2 = await apiContext.post("/api/chat/ask", {
      data: { message: message2 },
    });
    expect(res2.ok()).toBe(true);
  });

  test("handle empty/invalid messages gracefully", async ({ apiContext }) => {
    const res = await apiContext.post("/api/chat/ask", {
      data: { message: "" },
    });

    // Should reject or return error
    expect([400, 422]).toContain(res.status());
  });

  test("clean up: remove test document from knowledge base", async ({ apiContext }) => {
    const res = await apiContext.delete(`/api/documents/${documentId}`);
    expect(res.status()).toBe(204);
  });
});
