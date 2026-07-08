import { test } from "node:test";
import assert from "node:assert/strict";
import { ApiClient } from "../src/apiClient.js";
import { ApiError, NoOrgError, NotAuthenticatedError } from "../src/errors.js";

interface FetchCall {
  url: string;
  init: { method?: string; headers?: Record<string, string>; body?: string };
}

function installFetch(response: { status: number; body?: unknown; text?: string }): FetchCall[] {
  const calls: FetchCall[] = [];
  (globalThis as unknown as { fetch: unknown }).fetch = async (url: string, init: FetchCall["init"]) => {
    calls.push({ url, init });
    const text = response.text ?? (response.body !== undefined ? JSON.stringify(response.body) : "");
    return {
      status: response.status,
      ok: response.status >= 200 && response.status < 300,
      statusText: "STATUS",
      text: async () => text,
    };
  };
  return calls;
}

function makeClient(token: string | null = "tok", orgId: string | null = "org-1"): ApiClient {
  const cfg = { apiUrl: "http://api.test/api" } as never;
  const session = {
    getToken: async () => token,
    getOrgId: async () => orgId,
  } as never;
  return new ApiClient(cfg, session);
}

test("injects Authorization and X-Org-ID headers", async () => {
  const calls = installFetch({ status: 200, body: { ok: true } });
  await makeClient("tok", "org-1").get("/things");
  const h = calls[0]!.init.headers!;
  assert.equal(h.Authorization, "Bearer tok");
  assert.equal(h["X-Org-ID"], "org-1");
  assert.equal(calls[0]!.url, "http://api.test/api/things");
});

test("serializes query params, repeats arrays, drops undefined/null", async () => {
  const calls = installFetch({ status: 200, body: [] });
  await makeClient().get("/things", { query: { a: 1, b: undefined, c: null, d: ["p", "q"] } });
  const url = new URL(calls[0]!.url);
  assert.equal(url.searchParams.get("a"), "1");
  assert.equal(url.searchParams.has("b"), false);
  assert.equal(url.searchParams.has("c"), false);
  assert.deepEqual(url.searchParams.getAll("d"), ["p", "q"]);
});

test("sends JSON body with Content-Type", async () => {
  const calls = installFetch({ status: 201, body: { id: "1" } });
  await makeClient().post("/things", { body: { name: "x" } });
  assert.equal(calls[0]!.init.method, "POST");
  assert.equal(calls[0]!.init.headers!["Content-Type"], "application/json");
  assert.equal(calls[0]!.init.body, JSON.stringify({ name: "x" }));
});

test("204 returns null", async () => {
  installFetch({ status: 204 });
  const result = await makeClient().delete("/things/1");
  assert.equal(result, null);
});

test("throws NotAuthenticatedError when no token", async () => {
  installFetch({ status: 200, body: {} });
  await assert.rejects(() => makeClient(null).get("/things"), NotAuthenticatedError);
});

test("throws NoOrgError when org required but missing", async () => {
  installFetch({ status: 200, body: {} });
  await assert.rejects(() => makeClient("tok", null).get("/things"), NoOrgError);
});

test("requireOrg=false allows missing org and omits header", async () => {
  const calls = installFetch({ status: 200, body: { me: true } });
  await makeClient("tok", null).get("/users/me", { requireOrg: false });
  assert.equal(calls[0]!.init.headers!["X-Org-ID"], undefined);
});

test("surfaces backend detail as ApiError", async () => {
  installFetch({ status: 400, body: { detail: "X-Org-ID must be a valid UUID" } });
  await assert.rejects(
    () => makeClient().get("/things"),
    (err: unknown) => {
      assert.ok(err instanceof ApiError);
      assert.equal(err.status, 400);
      assert.equal(err.detail, "X-Org-ID must be a valid UUID");
      return true;
    },
  );
});
