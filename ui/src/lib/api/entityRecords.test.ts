import { beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.fn();
const post = vi.fn();
const patch = vi.fn();
const del = vi.fn();

vi.mock("./client", () => ({
  default: {
    get: (...a: unknown[]) => get(...a),
    post: (...a: unknown[]) => post(...a),
    patch: (...a: unknown[]) => patch(...a),
    delete: (...a: unknown[]) => del(...a),
  },
}));

import { createRecord, deleteRecord, filterToParam, listRecords, updateRecord } from "./entityRecords";

beforeEach(() => {
  [get, post, patch, del].forEach((m) => m.mockReset());
});

describe("filterToParam", () => {
  it("omits the value for isnull / empty-value ops", () => {
    expect(filterToParam({ field: "email", op: "isnull" })).toBe("email:isnull");
    expect(filterToParam({ field: "stage", op: "eq", value: "" })).toBe("stage:eq");
  });

  it("emits field:op:value otherwise", () => {
    expect(filterToParam({ field: "stage", op: "eq", value: "won" })).toBe("stage:eq:won");
    expect(filterToParam({ field: "tags", op: "in", value: "a,b,c" })).toBe("tags:in:a,b,c");
  });
});

describe("entityRecords API client", () => {
  it("lists records mapping search/cursor to query params", async () => {
    get.mockResolvedValue({ data: { items: [], next_cursor: null, limit: 50 } });
    await listRecords("widgets", { search: "abc", cursor: "c1", limit: 20 });
    expect(get).toHaveBeenCalledWith("/entities/widgets/records", {
      paramsSerializer: { indexes: null },
      params: { q: "abc", cursor: "c1", limit: 20 },
    });
  });

  it("omits an empty search and cursor (sends undefined, defaults limit)", async () => {
    get.mockResolvedValue({ data: { items: [], next_cursor: null, limit: 50 } });
    await listRecords("widgets");
    expect(get).toHaveBeenCalledWith("/entities/widgets/records", {
      paramsSerializer: { indexes: null },
      params: { q: undefined, cursor: undefined, limit: 50 },
    });
  });

  it("sends order_by + order_dir when orderBy is set", async () => {
    get.mockResolvedValue({ data: { items: [], next_cursor: null, limit: 50 } });
    await listRecords("widgets", { orderBy: "phase", orderDir: "asc" });
    expect(get).toHaveBeenCalledWith("/entities/widgets/records", {
      paramsSerializer: { indexes: null },
      params: expect.objectContaining({ order_by: "phase", order_dir: "asc" }),
    });
  });

  it("defaults order_dir to desc when orderBy is set without a direction", async () => {
    get.mockResolvedValue({ data: { items: [], next_cursor: null, limit: 50 } });
    await listRecords("widgets", { orderBy: "created_at" });
    expect(get).toHaveBeenCalledWith("/entities/widgets/records", {
      paramsSerializer: { indexes: null },
      params: expect.objectContaining({ order_by: "created_at", order_dir: "desc" }),
    });
  });

  it("serializes complete filters to repeated field:op[:value] params", async () => {
    get.mockResolvedValue({ data: { items: [], next_cursor: null, limit: 50 } });
    await listRecords("widgets", {
      filters: [
        { field: "stage", op: "eq", value: "won" },
        { field: "amount", op: "gte", value: "100" },
        { field: "email", op: "isnull" },
      ],
    });
    expect(get).toHaveBeenCalledWith(
      "/entities/widgets/records",
      expect.objectContaining({
        paramsSerializer: { indexes: null },
        params: expect.objectContaining({ filter: ["stage:eq:won", "amount:gte:100", "email:isnull"] }),
      }),
    );
  });

  it("creates a record via POST", async () => {
    post.mockResolvedValue({ data: { id: "1" } });
    await createRecord("widgets", { name: "x" });
    expect(post).toHaveBeenCalledWith("/entities/widgets/records", { name: "x" });
  });

  it("updates a record via PATCH", async () => {
    patch.mockResolvedValue({ data: { id: "1" } });
    await updateRecord("widgets", "1", { name: "y" });
    expect(patch).toHaveBeenCalledWith("/entities/widgets/records/1", { name: "y" });
  });

  it("deletes a record via DELETE", async () => {
    del.mockResolvedValue({ data: undefined });
    await deleteRecord("widgets", "1");
    expect(del).toHaveBeenCalledWith("/entities/widgets/records/1");
  });

  it("propagates client errors to the caller", async () => {
    get.mockRejectedValue(new Error("Request failed"));
    await expect(listRecords("widgets")).rejects.toThrow("Request failed");
  });
});
