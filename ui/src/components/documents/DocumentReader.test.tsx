import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { DocumentChunk } from "@/lib/api/documents";

import { DocumentReader } from "./DocumentReader";

const { getDocumentChunks, getDocumentContent } = vi.hoisted(() => ({
  getDocumentChunks: vi.fn(),
  getDocumentContent: vi.fn(),
}));

vi.mock("@/lib/api/documents", () => ({
  getDocumentChunks,
  getDocumentContent,
}));

function chunk(order: number): DocumentChunk {
  return {
    id: `c${order}`,
    chunk_order: order,
    text: `Chunk text ${order}`,
    summary: `Summary ${order}`,
  } as DocumentChunk;
}

beforeEach(() => {
  vi.stubGlobal(
    "IntersectionObserver",
    vi.fn(() => ({ observe: vi.fn(), disconnect: vi.fn(), unobserve: vi.fn() })),
  );
  Element.prototype.scrollIntoView = vi.fn();
  getDocumentContent.mockResolvedValue({
    content: null,
    format: null,
    kind: "other",
    original_url: null,
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

describe("DocumentReader citation target", () => {
  it("highlights the cited chunk and scrolls it into view", async () => {
    getDocumentChunks.mockResolvedValue({ chunks: [chunk(0), chunk(1), chunk(2)], total: 3 });

    render(
      <DocumentReader
        documentId="d1"
        documentTitle="Doc"
        summaryTree={null}
        open
        onClose={() => {}}
        targetChunkOrder={2}
      />,
    );

    await waitFor(() => expect(screen.getByText("Chunk text 2")).toBeInTheDocument());
    const target = document.getElementById("reader-chunk-2");
    expect(target).not.toBeNull();
    expect(target?.className).toContain("border-primary");
    await waitFor(() => expect(Element.prototype.scrollIntoView).toHaveBeenCalled());
    // Non-target chunks are not highlighted.
    expect(document.getElementById("reader-chunk-1")?.className ?? "").not.toContain(
      "border-primary",
    );
  });

  it("keeps loading pages until the cited chunk is present", async () => {
    getDocumentChunks
      .mockResolvedValueOnce({ chunks: [chunk(0), chunk(1)], total: 4 })
      .mockResolvedValueOnce({ chunks: [chunk(2), chunk(3)], total: 4 });

    render(
      <DocumentReader
        documentId="d1"
        documentTitle="Doc"
        summaryTree={null}
        open
        onClose={() => {}}
        targetChunkOrder={3}
      />,
    );

    await waitFor(() => expect(screen.getByText("Chunk text 3")).toBeInTheDocument());
    expect(getDocumentChunks).toHaveBeenCalledTimes(2);
  });

  it("does not scroll or page-chase without a citation target", async () => {
    getDocumentChunks.mockResolvedValue({ chunks: [chunk(0), chunk(1)], total: 2 });

    render(
      <DocumentReader
        documentId="d1"
        documentTitle="Doc"
        summaryTree={null}
        open
        onClose={() => {}}
      />,
    );

    await waitFor(() => expect(screen.getByText("Chunk text 0")).toBeInTheDocument());
    expect(Element.prototype.scrollIntoView).not.toHaveBeenCalled();
  });
});
