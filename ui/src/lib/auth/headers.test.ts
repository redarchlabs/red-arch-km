import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const getToken = vi.fn();
vi.mock("./clerk", () => ({ getToken: () => getToken() }));

import { authHeaders } from "./headers";

describe("authHeaders", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    getToken.mockReset();
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  describe("with bypass OFF (the default)", () => {
    beforeEach(() => {
      vi.stubEnv("NEXT_PUBLIC_BYPASS_AUTH", undefined);
    });

    it("sends the Clerk bearer token", async () => {
      getToken.mockResolvedValue("tok");
      expect(await authHeaders()).toEqual({ Authorization: "Bearer tok" });
    });

    it("sends nothing when Clerk has no session", async () => {
      getToken.mockResolvedValue(null);
      expect(await authHeaders()).toEqual({});
    });

    it("never sends test-user headers, even with a secret sitting in storage", async () => {
      // The guarantee that matters: a stale secret in a normal build must not become a
      // credential. Only the build-time flag can open that door.
      window.sessionStorage.setItem("redarch:bypassSecret", "s3cret");
      getToken.mockResolvedValue("tok");
      const headers = await authHeaders();
      expect(headers).toEqual({ Authorization: "Bearer tok" });
      expect(headers).not.toHaveProperty("X-Test-User");
    });
  });

  describe("with bypass ON", () => {
    beforeEach(() => {
      vi.stubEnv("NEXT_PUBLIC_BYPASS_AUTH", "1");
    });

    it("sends the test-user pair and never asks Clerk for a token", async () => {
      window.sessionStorage.setItem("redarch:bypassSecret", "s3cret");
      expect(await authHeaders()).toEqual({
        "X-Test-User": "siteadmin",
        "X-Test-Secret": "s3cret",
      });
      // Calling Clerk offline is what hangs; the whole point is not to.
      expect(getToken).not.toHaveBeenCalled();
    });

    it("sends nothing before the operator has entered the key", async () => {
      expect(await authHeaders()).toEqual({});
      expect(getToken).not.toHaveBeenCalled();
    });
  });
});
