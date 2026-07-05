import { AxiosError, AxiosHeaders } from "axios";
import { describe, expect, it } from "vitest";

import { getApiErrorMessage, getApiErrorStatus } from "./errors";

function axiosErrorWith(status: number, data: unknown): AxiosError {
  const headers = new AxiosHeaders();
  return new AxiosError("Request failed", "ERR_BAD_REQUEST", undefined, undefined, {
    status,
    statusText: "",
    headers,
    config: { headers },
    data,
  });
}

describe("getApiErrorMessage", () => {
  it("prefers the FastAPI detail field", () => {
    const error = axiosErrorWith(409, { detail: "Setup already completed" });
    expect(getApiErrorMessage(error, "fallback")).toBe("Setup already completed");
  });

  it("falls back to the Error message when detail is missing", () => {
    const error = axiosErrorWith(500, { unrelated: true });
    expect(getApiErrorMessage(error, "fallback")).toBe("Request failed");
  });

  it("uses the fallback for non-Error values", () => {
    expect(getApiErrorMessage("boom", "fallback")).toBe("fallback");
    expect(getApiErrorMessage(undefined, "fallback")).toBe("fallback");
  });

  it("ignores a non-string detail", () => {
    const error = axiosErrorWith(422, { detail: [{ msg: "field required" }] });
    expect(getApiErrorMessage(error, "fallback")).toBe("Request failed");
  });
});

describe("getApiErrorStatus", () => {
  it("returns the HTTP status for axios errors", () => {
    expect(getApiErrorStatus(axiosErrorWith(403, {}))).toBe(403);
  });

  it("returns null for non-axios errors", () => {
    expect(getApiErrorStatus(new Error("x"))).toBeNull();
  });
});
