import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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
  createForm,
  deleteForm,
  generateFormLink,
  getForm,
  getPublicForm,
  listFormLinks,
  listForms,
  submitPublicForm,
  updateForm,
} from "./forms";

const fetchMock = vi.fn();

beforeEach(() => {
  get.mockReset();
  post.mockReset();
  patch.mockReset();
  del.mockReset();
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("forms admin client (authenticated)", () => {
  it("lists forms via GET /forms/", async () => {
    get.mockResolvedValue({ data: [{ id: "fm1" }] });
    const res = await listForms();
    expect(get).toHaveBeenCalledWith("/forms/");
    expect(res).toEqual([{ id: "fm1" }]);
  });

  it("gets one form via GET /forms/:id", async () => {
    get.mockResolvedValue({ data: { id: "fm1" } });
    await getForm("fm1");
    expect(get).toHaveBeenCalledWith("/forms/fm1");
  });

  it("creates a form via POST /forms/", async () => {
    post.mockResolvedValue({ data: { id: "fm1" } });
    const input = { name: "Intake", slug: "intake", entity_definition_id: "ed1" };
    await createForm(input);
    expect(post).toHaveBeenCalledWith("/forms/", input);
  });

  it("updates a form via PATCH /forms/:id", async () => {
    patch.mockResolvedValue({ data: { id: "fm1" } });
    await updateForm("fm1", { name: "Renamed" });
    expect(patch).toHaveBeenCalledWith("/forms/fm1", { name: "Renamed" });
  });

  it("deletes a form via DELETE /forms/:id", async () => {
    del.mockResolvedValue({ data: undefined });
    await deleteForm("fm1");
    expect(del).toHaveBeenCalledWith("/forms/fm1");
  });

  it("lists links via GET /forms/:id/links", async () => {
    get.mockResolvedValue({ data: [] });
    await listFormLinks("fm1");
    expect(get).toHaveBeenCalledWith("/forms/fm1/links");
  });

  it("mints a link via POST /forms/:id/links", async () => {
    post.mockResolvedValue({ data: { token: "t", url: "u" } });
    const input = { target_record_id: "r1" };
    const res = await generateFormLink("fm1", input);
    expect(post).toHaveBeenCalledWith("/forms/fm1/links", input);
    expect(res).toMatchObject({ token: "t" });
  });
});

describe("public forms client (unauthenticated fetch)", () => {
  it("fetches a public form, URL-encoding the token and disabling cache", async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ form_name: "Intake" }) });

    const res = await getPublicForm("a b/c");

    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toContain("/public/forms/a%20b%2Fc");
    expect(init).toMatchObject({ cache: "no-store" });
    expect(res).toEqual({ form_name: "Intake" });
  });

  it("throws the server-provided detail on a failed load", async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 404,
      json: async () => ({ detail: "Link expired" }),
    });

    await expect(getPublicForm("tok")).rejects.toThrow("Link expired");
  });

  it("falls back to a status message when the error body has no detail", async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => {
        throw new Error("not json");
      },
    });

    await expect(getPublicForm("tok")).rejects.toThrow("Request failed (500)");
  });

  it("submits via POST with a JSON body", async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({}) });
    const body = { values: { a: 1 }, related: {} };

    await submitPublicForm("tok", body);

    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toContain("/public/forms/tok");
    expect(init).toMatchObject({ method: "POST" });
    expect(JSON.parse((init as RequestInit).body as string)).toEqual(body);
  });

  it("throws the server detail on a failed submit", async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 422,
      json: async () => ({ detail: "name is required" }),
    });

    await expect(submitPublicForm("tok", { values: {}, related: {} })).rejects.toThrow("name is required");
  });
});
