import { describe, expect, it } from "vitest";

import { safeReturnTo } from "./returnTo";

describe("safeReturnTo", () => {
  const FALLBACK = "/documents";

  it("keeps a same-site path with its query string", () => {
    expect(safeReturnTo("/views/abc/kiosk?record_id=123", FALLBACK)).toBe(
      "/views/abc/kiosk?record_id=123"
    );
  });

  it("falls back for the sign-in page itself, which would loop", () => {
    expect(safeReturnTo("/login", FALLBACK)).toBe(FALLBACK);
    expect(safeReturnTo("/login?next=/x", FALLBACK)).toBe(FALLBACK);
  });

  it("falls back for an empty or root path", () => {
    expect(safeReturnTo("", FALLBACK)).toBe(FALLBACK);
    expect(safeReturnTo("/", FALLBACK)).toBe(FALLBACK);
  });

  describe("open redirect", () => {
    // A return-to value is attacker-supplied in the general case: it arrives in a
    // URL someone can send. Anything that leaves this origin must be refused.
    it.each([
      "//evil.example/phish",
      "///evil.example",
      "https://evil.example/phish",
      "http://evil.example",
      "javascript:alert(1)",
      "/\\evil.example",
      "\\\\evil.example",
      " //evil.example",
      "/%2F%2Fevil.example",
    ])("refuses %j", (target) => {
      expect(safeReturnTo(target, FALLBACK)).toBe(FALLBACK);
    });
  });
});
