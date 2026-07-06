import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.fn();
const post = vi.fn();
const patch = vi.fn();
const put = vi.fn();
const del = vi.fn();

vi.mock("./client", () => ({
  default: {
    get: (...a: unknown[]) => get(...a),
    post: (...a: unknown[]) => post(...a),
    patch: (...a: unknown[]) => patch(...a),
    put: (...a: unknown[]) => put(...a),
    delete: (...a: unknown[]) => del(...a),
  },
}));

import {
  createDocument,
  deleteDocument,
  getDocumentChunks,
  getDocumentContent,
  listDocuments,
  updateDocument,
  updateDocumentContent,
  uploadDocument,
} from "./documents";

beforeEach(() => {
  get.mockReset();
  post.mockReset();
  patch.mockReset();
  put.mockReset();
  del.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("documents client", () => {
  it("lists documents with pagination and no folder filter by default", async () => {
    get.mockResolvedValue({ data: { items: [], total: 0 } });
    await listDocuments();
    expect(get).toHaveBeenCalledWith("/documents/", { params: { page: 1, page_size: 20 } });
  });

  it("scopes the list to a folder when a folderId is given", async () => {
    get.mockResolvedValue({ data: { items: [], total: 0 } });
    await listDocuments(2, 200, "f1");
    expect(get).toHaveBeenCalledWith("/documents/", {
      params: { page: 2, page_size: 200, folder_id: "f1" },
    });
  });

  it("updates document metadata via PATCH", async () => {
    patch.mockResolvedValue({ data: { id: "d1" } });
    await updateDocument("d1", { title: "New" });
    expect(patch).toHaveBeenCalledWith("/documents/d1", { title: "New" });
  });

  it("replaces document body via PUT /content", async () => {
    put.mockResolvedValue({ data: { id: "d1" } });
    await updateDocumentContent("d1", "# hello");
    expect(put).toHaveBeenCalledWith("/documents/d1/content", { text: "# hello" });
  });

  it("creates a document via POST", async () => {
    post.mockResolvedValue({ data: { id: "d1" } });
    await createDocument({ title: "Runbook.md", text: "body" });
    expect(post).toHaveBeenCalledWith("/documents/", { title: "Runbook.md", text: "body" });
  });

  it("deletes a document via DELETE", async () => {
    del.mockResolvedValue({ data: undefined });
    await deleteDocument("d1");
    expect(del).toHaveBeenCalledWith("/documents/d1");
  });

  it("fetches a page of chunks with offset/limit params", async () => {
    get.mockResolvedValue({ data: { chunks: [], total: 0 } });
    await getDocumentChunks("d1", { offset: 50, limit: 25 });
    expect(get).toHaveBeenCalledWith("/documents/d1/chunks", {
      params: { offset: 50, limit: 25 },
    });
  });

  it("defaults chunk paging to offset 0 / limit 50", async () => {
    get.mockResolvedValue({ data: { chunks: [], total: 0 } });
    await getDocumentChunks("d1");
    expect(get).toHaveBeenCalledWith("/documents/d1/chunks", {
      params: { offset: 0, limit: 50 },
    });
  });

  it("fetches original content via GET /content", async () => {
    get.mockResolvedValue({ data: { content: "x", format: "markdown" } });
    await getDocumentContent("d1");
    expect(get).toHaveBeenCalledWith("/documents/d1/content");
  });

  it("normalizes a single-file upload into one document", async () => {
    post.mockResolvedValue({ data: { id: "d1" } });
    const file = new File(["hi"], "a.txt", { type: "text/plain" });

    const res = await uploadDocument({ file, title: "a.txt" });

    const [url, form, config] = post.mock.calls[0]!;
    expect(url).toBe("/documents/upload");
    expect(form).toBeInstanceOf(FormData);
    // Multipart must let axios set the boundary itself.
    expect((config as { headers: Record<string, unknown> }).headers["Content-Type"]).toBeUndefined();
    expect(res).toEqual({ documents: [{ id: "d1" }], skipped: [] });
  });

  it("normalizes a .zip batch upload into many documents plus skips", async () => {
    post.mockResolvedValue({
      data: { batch: true, created: 2, skipped: ["big.bin"], documents: [{ id: "a" }, { id: "b" }] },
    });
    const file = new File(["zip"], "bundle.zip");

    const res = await uploadDocument({ file, title: "bundle.zip" });

    expect(res).toEqual({ documents: [{ id: "a" }, { id: "b" }], skipped: ["big.bin"] });
  });
});
