import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Document, PaginatedResponse } from "@/types";

import { useFolderDocuments } from "./useFolderDocuments";

const listDocuments = vi.fn();

vi.mock("@/lib/api/documents", () => ({
  listDocuments: (...a: unknown[]) => listDocuments(...a),
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

function doc(id: string): Document {
  return { id, title: id, processing_status: "SUCCESS" } as unknown as Document;
}

function page(items: Document[]): PaginatedResponse<Document> {
  return { items, total: items.length, page: 1, page_size: 200, pages: 1 };
}

beforeEach(() => {
  listDocuments.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("useFolderDocuments", () => {
  it("loads the current folder's documents", async () => {
    listDocuments.mockResolvedValue(page([doc("a"), doc("b")]));
    const { result } = renderHook(({ id }) => useFolderDocuments(id), {
      initialProps: { id: "f1" as string | null },
    });

    await waitFor(() => expect(result.current.docs).toHaveLength(2));
    expect(result.current.error).toBeNull();
    expect(listDocuments).toHaveBeenCalledWith(1, 200, "f1");
  });

  it("ignores a stale in-flight load when the folder switches (no overwrite)", async () => {
    const first = deferred<PaginatedResponse<Document>>();
    const second = deferred<PaginatedResponse<Document>>();
    listDocuments.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);

    const { result, rerender } = renderHook(({ id }) => useFolderDocuments(id), {
      initialProps: { id: "f1" as string | null },
    });

    // Switch folders while f1's request is still in flight.
    rerender({ id: "f2" });
    await waitFor(() => expect(listDocuments).toHaveBeenCalledTimes(2));

    // The current folder (f2) resolves first...
    await act(async () => {
      second.resolve(page([doc("f2-doc")]));
    });
    // ...and the stale f1 response arrives late. It must NOT clobber f2.
    await act(async () => {
      first.resolve(page([doc("f1-doc")]));
    });

    expect(result.current.docs.map((d) => d.id)).toEqual(["f2-doc"]);
  });

  it("surfaces a load failure as an error message", async () => {
    listDocuments.mockRejectedValue(new Error("boom"));
    const { result } = renderHook(({ id }) => useFolderDocuments(id), {
      initialProps: { id: "f1" as string | null },
    });

    await waitFor(() => expect(result.current.error).toBe("boom"));
    expect(result.current.docs).toEqual([]);
  });
});
