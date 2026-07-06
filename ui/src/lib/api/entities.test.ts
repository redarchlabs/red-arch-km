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

import {
  addEntityField,
  createRelationship,
  deleteEntityField,
  listEntities,
  listRelationships,
} from "./entities";

beforeEach(() => {
  [get, post, patch, del].forEach((m) => m.mockReset());
});

describe("entities API client", () => {
  it("lists entities from the paginated envelope's items", async () => {
    get.mockResolvedValue({ data: { items: [{ id: "e1" }], total: 1, page: 1, page_size: 200, pages: 1 } });
    await expect(listEntities()).resolves.toEqual([{ id: "e1" }]);
    expect(get).toHaveBeenCalledWith("/entity-definitions/", { params: { page_size: 200 } });
  });

  it("adds a field via POST to the fields sub-resource", async () => {
    post.mockResolvedValue({ data: { id: "f1" } });
    const input = { name: "Status", slug: "status", field_type: "text" as const };
    await addEntityField("e1", input);
    expect(post).toHaveBeenCalledWith("/entity-definitions/e1/fields", input);
  });

  it("deletes a field via DELETE", async () => {
    del.mockResolvedValue({ data: undefined });
    await deleteEntityField("e1", "f1");
    expect(del).toHaveBeenCalledWith("/entity-definitions/e1/fields/f1");
  });

  it("lists relationships for an entity", async () => {
    get.mockResolvedValue({ data: [] });
    await listRelationships("e1");
    expect(get).toHaveBeenCalledWith("/entity-definitions/e1/relationships");
  });

  it("creates a relationship via POST", async () => {
    post.mockResolvedValue({ data: { id: "r1" } });
    const input = {
      name: "Orders",
      slug: "orders",
      cardinality: "one_to_many" as const,
      target_definition_id: "e2",
    };
    await createRelationship("e1", input);
    expect(post).toHaveBeenCalledWith("/entity-definitions/e1/relationships", input);
  });

  it("propagates client errors to the caller", async () => {
    get.mockRejectedValue(new Error("Request failed"));
    await expect(listEntities()).rejects.toThrow("Request failed");
  });
});
