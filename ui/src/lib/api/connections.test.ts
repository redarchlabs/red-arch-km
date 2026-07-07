import { beforeEach, describe, expect, it, vi } from "vitest";

// Mock the axios client so these tests assert path/method wiring without a
// backend or Clerk token flow.
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
  buildConnectionCreate,
  buildConnectionUpdate,
  connectionToForm,
  createConnection,
  deleteConnection,
  EMPTY_CONNECTION_FORM,
  listConnections,
  normalizeConnectionConfig,
  updateConnection,
  type Connection,
  type ConnectionFormState,
} from "./connections";

beforeEach(() => {
  [get, post, patch, del].forEach((m) => m.mockReset());
});

const CONN: Connection = {
  id: "c1",
  name: "Stripe",
  kind: "http",
  base_url: "https://api.stripe.com",
  auth_type: "bearer",
  config: {},
  has_secret: true,
};

describe("connections API client", () => {
  it("lists connections and unwraps data", async () => {
    get.mockResolvedValue({ data: [CONN] });
    await expect(listConnections()).resolves.toEqual([CONN]);
    expect(get).toHaveBeenCalledWith("/workflows/connections");
  });

  it("creates a connection via POST", async () => {
    post.mockResolvedValue({ data: CONN });
    const body = { name: "Stripe", auth_type: "bearer" as const };
    await createConnection(body);
    expect(post).toHaveBeenCalledWith("/workflows/connections", body);
  });

  it("updates a connection via PATCH under its id", async () => {
    patch.mockResolvedValue({ data: CONN });
    await updateConnection("c1", { name: "Renamed" });
    expect(patch).toHaveBeenCalledWith("/workflows/connections/c1", { name: "Renamed" });
  });

  it("deletes a connection via DELETE under its id", async () => {
    del.mockResolvedValue({ data: undefined });
    await deleteConnection("c1");
    expect(del).toHaveBeenCalledWith("/workflows/connections/c1");
  });

  it("propagates client errors to the caller", async () => {
    get.mockRejectedValue(new Error("Request failed"));
    await expect(listConnections()).rejects.toThrow("Request failed");
  });
});

describe("connection payload builders (write-only secret)", () => {
  const base: ConnectionFormState = {
    ...EMPTY_CONNECTION_FORM,
    name: "  Stripe  ",
    base_url: "  https://api.stripe.com  ",
    auth_type: "bearer",
  };

  it("create trims name/base_url and omits an empty secret", () => {
    expect(buildConnectionCreate(base)).toEqual({
      kind: "http",
      name: "Stripe",
      base_url: "https://api.stripe.com",
      auth_type: "bearer",
      config: {},
    });
  });

  it("create includes a secret only when one was entered", () => {
    expect(buildConnectionCreate({ ...base, secret: "sk_live_x" })).toMatchObject({
      secret: "sk_live_x",
    });
  });

  it("create maps an empty base_url to null", () => {
    expect(buildConnectionCreate({ ...base, base_url: "   " })).toMatchObject({ base_url: null });
  });

  it("update omits the secret when it was NOT edited (keeps the stored one)", () => {
    const form = connectionToForm(CONN); // secret blank, secretDirty false
    const body = buildConnectionUpdate(form);
    expect(body).not.toHaveProperty("secret");
    expect(body).toMatchObject({ name: "Stripe", auth_type: "bearer" });
  });

  it("update sends a new secret only once the field is dirtied", () => {
    const form: ConnectionFormState = { ...connectionToForm(CONN), secret: "new", secretDirty: true };
    expect(buildConnectionUpdate(form)).toMatchObject({ secret: "new" });
  });

  it("update ignores a dirtied-but-blank secret (does not clear it)", () => {
    const form: ConnectionFormState = { ...connectionToForm(CONN), secret: "", secretDirty: true };
    expect(buildConnectionUpdate(form)).not.toHaveProperty("secret");
  });
});

describe("normalizeConnectionConfig", () => {
  it("keeps only header for api_key and drops it when blank", () => {
    expect(normalizeConnectionConfig("api_key", { header: "X-Api-Key", username: "x" })).toEqual({
      header: "X-Api-Key",
    });
    expect(normalizeConnectionConfig("api_key", { header: "  " })).toEqual({});
  });

  it("keeps only username for basic and drops everything else", () => {
    expect(normalizeConnectionConfig("basic", { username: "admin", header: "x" })).toEqual({
      username: "admin",
    });
  });

  it("drops all auth extras for none/bearer", () => {
    expect(normalizeConnectionConfig("none", { header: "x", username: "y" })).toEqual({});
    expect(normalizeConnectionConfig("bearer", { header: "x" })).toEqual({});
  });
});
