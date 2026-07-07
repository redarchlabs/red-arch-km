import { describe, expect, it } from "vitest";

import {
  applyHttpField,
  applyHttpObjectField,
  HTTP_METHODS,
  readHttpObject,
  readHttpRequest,
} from "./httpRequest";

describe("readHttpRequest", () => {
  it("applies defaults for an empty node", () => {
    expect(readHttpRequest({})).toEqual({
      connection: "",
      method: "GET",
      path: "",
      url: "",
      capture: "",
    });
  });

  it("reads config fields and the TOP-LEVEL capture", () => {
    const data = {
      capture: "resp",
      config: { connection: "Stripe", method: "post", path: "/charges", url: "" },
    };
    expect(readHttpRequest(data)).toEqual({
      connection: "Stripe",
      method: "POST", // normalized to upper-case
      path: "/charges",
      url: "",
      capture: "resp",
    });
  });

  it("falls back to GET for an unknown method", () => {
    expect(readHttpRequest({ config: { method: "TELEPORT" } }).method).toBe("GET");
  });

  it("tolerates a non-object config", () => {
    expect(readHttpRequest({ config: "nope" }).method).toBe("GET");
  });
});

describe("applyHttpField (immutable, prunes blanks)", () => {
  it("writes connection into config without mutating the input", () => {
    const data = { config: { method: "GET" } };
    const next = applyHttpField(data, "connection", "Stripe");
    expect(next).toEqual({ config: { method: "GET", connection: "Stripe" } });
    expect(data).toEqual({ config: { method: "GET" } }); // untouched
  });

  it("stores capture at the TOP LEVEL, not inside config", () => {
    const next = applyHttpField({ config: {} }, "capture", "resp");
    expect(next).toMatchObject({ capture: "resp", config: {} });
  });

  it("prunes an emptied connection/path/url/capture", () => {
    expect(applyHttpField({ config: { connection: "x" } }, "connection", "")).toEqual({ config: {} });
    expect(applyHttpField({ capture: "r", config: {} }, "capture", "   ")).toEqual({ config: {} });
  });

  it("always keeps method (from a fixed select) and normalizes it", () => {
    expect(applyHttpField({ config: {} }, "method", "patch")).toEqual({
      config: { method: "PATCH" },
    });
  });

  it("trims stored scalar values", () => {
    expect(applyHttpField({ config: {} }, "path", "  /v1/ping  ")).toEqual({
      config: { path: "/v1/ping" },
    });
  });
});

describe("applyHttpObjectField (headers/body)", () => {
  it("sets a non-empty object under config", () => {
    expect(applyHttpObjectField({ config: {} }, "headers", { "X-Trace": "1" })).toEqual({
      config: { headers: { "X-Trace": "1" } },
    });
  });

  it("removes the key when the object is empty", () => {
    expect(applyHttpObjectField({ config: { body: { a: 1 } } }, "body", {})).toEqual({
      config: {},
    });
  });

  it("preserves other config keys and does not mutate input", () => {
    const data = { config: { method: "POST" } };
    const next = applyHttpObjectField(data, "headers", { A: "b" });
    expect(next).toEqual({ config: { method: "POST", headers: { A: "b" } } });
    expect(data).toEqual({ config: { method: "POST" } });
  });
});

describe("readHttpObject", () => {
  it("returns the stored headers/body object", () => {
    expect(readHttpObject({ config: { headers: { A: "1" } } }, "headers")).toEqual({ A: "1" });
    expect(readHttpObject({}, "body")).toBeUndefined();
  });
});

describe("HTTP_METHODS", () => {
  it("offers the standard verbs", () => {
    expect(HTTP_METHODS).toEqual(["GET", "POST", "PUT", "PATCH", "DELETE"]);
  });
});
