import { describe, expect, it } from "vitest";

import { fillTokens, safeHref } from "./href";

describe("safeHref", () => {
  it("passes relative and http(s) URLs", () => {
    expect(safeHref("/views/x/view")).toBe("/views/x/view");
    expect(safeHref("https://example.com/x")).toBe("https://example.com/x");
    expect(safeHref("#anchor")).toBe("#anchor");
  });
  it("neutralizes dangerous schemes to #", () => {
    expect(safeHref("javascript:alert(1)")).toBe("#");
    expect(safeHref(" JavaScript:alert(1)")).toBe("#");
    expect(safeHref("data:text/html,x")).toBe("#");
    expect(safeHref("vbscript:x")).toBe("#");
  });
});

describe("fillTokens", () => {
  it("fills {id} and {field} tokens, URL-encoded", () => {
    const out = fillTokens("/views/{player_view_slug}/view?record_id={id}", {
      id: "abc-123",
      player_view_slug: "course play/1",
    });
    expect(out).toBe("/views/course%20play%2F1/view?record_id=abc-123");
  });
  it("resolves an absent token to an empty string", () => {
    expect(fillTokens("/views/{missing}/view", { id: "1" })).toBe("/views//view");
  });
  it("scheme-checks the filled result (a token can't smuggle javascript:)", () => {
    // Even if a value were malicious, the encoded value can't form a new scheme.
    expect(fillTokens("{evil}", { evil: "javascript:alert(1)" })).toBe(
      "javascript%3Aalert(1)",
    );
    // A template whose static scheme is dangerous is neutralized.
    expect(fillTokens("javascript:{x}", { x: "1" })).toBe("#");
  });
});
