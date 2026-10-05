import { describe, expect, it, vi } from "vitest";

import { fetchAllPages } from "./pagination";

function pages<T>(all: T[], pageSize: number) {
  return vi.fn(async (page: number) => ({
    items: all.slice((page - 1) * pageSize, page * pageSize),
    total: all.length,
  }));
}

describe("fetchAllPages", () => {
  it("loads every page until the total is reached", async () => {
    const all = Array.from({ length: 450 }, (_, i) => i);
    const fetchPage = pages(all, 200);
    const result = await fetchAllPages(fetchPage, { maxPages: 10 });
    expect(result.items).toEqual(all);
    expect(result.total).toBe(450);
    expect(result.truncated).toBe(false);
    expect(fetchPage).toHaveBeenCalledTimes(3);
  });

  it("stops at the page cap and says the list is truncated", async () => {
    const all = Array.from({ length: 1000 }, (_, i) => i);
    const result = await fetchAllPages(pages(all, 200), { maxPages: 2 });
    expect(result.items).toHaveLength(400);
    expect(result.total).toBe(1000);
    expect(result.truncated).toBe(true);
  });

  it("stops on an empty page even if the total says more", async () => {
    const fetchPage = vi.fn(async (page: number) => ({ items: page === 1 ? [1, 2] : [], total: 99 }));
    const result = await fetchAllPages(fetchPage, { maxPages: 10 });
    expect(result.items).toEqual([1, 2]);
    expect(result.truncated).toBe(true);
    expect(fetchPage).toHaveBeenCalledTimes(2);
  });
});
