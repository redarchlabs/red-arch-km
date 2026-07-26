import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Document, PaginatedResponse } from "@/types";

import DocumentsPage from "./page";

const listDocuments = vi.fn();
const listFolders = vi.fn();
let currentOrgId: string | null = "org-1";

vi.mock("@/lib/api/documents", () => ({
  listDocuments: (...a: unknown[]) => listDocuments(...a),
}));
vi.mock("@/lib/api/folders", () => ({
  listFolders: (...a: unknown[]) => listFolders(...a),
}));
vi.mock("@/context/OrgContext", () => ({
  useOrg: () => ({ currentOrgId, isLoading: false }),
}));
// The row and upload dialog pull in editors/uploads that aren't under test.
vi.mock("@/components/documents/DocumentRow", () => ({
  DocumentRow: ({ doc }: { doc: Document }) => <div>{doc.title}</div>,
}));
vi.mock("@/components/documents/DocumentUpload", () => ({
  DocumentUpload: () => null,
}));

const PAGE_SIZE = 20;
const TOTAL = 67;

function doc(id: string): Document {
  return { id, title: id, processing_status: "SUCCESS" } as unknown as Document;
}

/** One server page of a 67-document list (the reported bug's shape). */
function serverPage(page: number): PaginatedResponse<Document> {
  const start = (page - 1) * PAGE_SIZE;
  const count = Math.max(0, Math.min(PAGE_SIZE, TOTAL - start));
  return {
    items: Array.from({ length: count }, (_, i) => doc(`doc-${start + i + 1}`)),
    total: TOTAL,
    page,
    page_size: PAGE_SIZE,
    pages: Math.ceil(TOTAL / PAGE_SIZE),
  };
}

beforeEach(() => {
  currentOrgId = "org-1";
  listDocuments.mockReset();
  listFolders.mockReset();
  listFolders.mockResolvedValue([]);
  listDocuments.mockImplementation((page: number) => Promise.resolve(serverPage(page)));
});

afterEach(() => {
  cleanup();
});

describe("DocumentsPage", () => {
  it("pages through a list longer than one page", async () => {
    render(<DocumentsPage />);

    await screen.findByText("doc-1");
    expect(listDocuments).toHaveBeenCalledWith(1, PAGE_SIZE);
    expect(screen.getByText(/Page 1 of 4/)).toBeTruthy();
    expect(screen.queryByText("doc-21")).toBeNull();

    fireEvent.click(screen.getByLabelText("Next page"));

    await screen.findByText("doc-21");
    expect(listDocuments).toHaveBeenCalledWith(2, PAGE_SIZE);
    expect(screen.queryByText("doc-1")).toBeNull();
  });

  it("reaches the final partial page", async () => {
    render(<DocumentsPage />);
    await screen.findByText("doc-1");

    for (const expected of [2, 3, 4]) {
      fireEvent.click(screen.getByLabelText("Next page"));
      await waitFor(() =>
        expect(screen.getByText(new RegExp(`Page ${expected} of 4`))).toBeTruthy(),
      );
    }

    expect(await screen.findByText("doc-67")).toBeTruthy();
    expect((screen.getByLabelText("Next page") as HTMLButtonElement).disabled).toBe(true);
  });

  it("steps back when the current page no longer exists", async () => {
    render(<DocumentsPage />);
    await screen.findByText("doc-1");
    fireEvent.click(screen.getByLabelText("Next page"));
    await screen.findByText("doc-21");

    // The list shrinks to a single page (e.g. someone deleted the rest).
    listDocuments.mockResolvedValue({
      items: [doc("only")],
      total: 1,
      page: 1,
      page_size: PAGE_SIZE,
      pages: 1,
    });
    fireEvent.click(screen.getByLabelText("Previous page"));

    expect(await screen.findByText("only")).toBeTruthy();
    await waitFor(() => expect(listDocuments).toHaveBeenLastCalledWith(1, PAGE_SIZE));
  });
});
