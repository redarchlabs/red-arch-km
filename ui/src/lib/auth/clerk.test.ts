import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TOKEN_TIMEOUT_MS, getToken } from "./clerk";

function setClerk(session: unknown): void {
  (window as unknown as { Clerk?: unknown }).Clerk = { session };
}

beforeEach(() => {
  delete (window as unknown as { Clerk?: unknown }).Clerk;
});

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe("getToken", () => {
  it("returns null before Clerk has mounted a session", async () => {
    await expect(getToken()).resolves.toBeNull();

    setClerk(null);
    await expect(getToken()).resolves.toBeNull();
  });

  it("returns the minted token", async () => {
    setClerk({ getToken: vi.fn().mockResolvedValue("jwt-abc") });

    await expect(getToken()).resolves.toBe("jwt-abc");
  });

  it("returns null when Clerk rejects, so the API 401 drives the login redirect", async () => {
    setClerk({ getToken: vi.fn().mockRejectedValue(new Error("no such template")) });

    await expect(getToken()).resolves.toBeNull();
  });

  it("rejects instead of hanging when Clerk never settles", async () => {
    vi.useFakeTimers();
    // A token mint that never settles used to stall the axios request
    // interceptor forever — the request was never dispatched, so axios's own
    // timeout never started and callers waited on a promise that never
    // resolved.
    setClerk({ getToken: vi.fn().mockReturnValue(new Promise(() => {})) });

    const pending = getToken();
    const assertion = expect(pending).rejects.toThrow(/timed out/i);
    await vi.advanceTimersByTimeAsync(TOKEN_TIMEOUT_MS);
    await assertion;
  });

  it("does not reject a mint that settles inside the timeout", async () => {
    vi.useFakeTimers();
    setClerk({
      getToken: vi.fn().mockReturnValue(
        new Promise((resolve) => {
          setTimeout(() => resolve("jwt-slow"), TOKEN_TIMEOUT_MS - 1);
        }),
      ),
    });

    const pending = getToken();
    await vi.advanceTimersByTimeAsync(TOKEN_TIMEOUT_MS);
    await expect(pending).resolves.toBe("jwt-slow");
  });
});
