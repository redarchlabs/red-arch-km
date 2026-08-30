import { describe, expect, it } from "vitest";

import { isApiAsset, resolveAssetUrl } from "./assets";

/**
 * A view's config names an org asset once. Which route that resolves to depends
 * on who is looking, and getting it wrong is not cosmetic: sending session
 * headers to the public route, or the org route to an anonymous visitor, both
 * fail — silently, as a model that never appears.
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";
const TOKEN = "H6oIIfZ3-HDnnKY8";

describe("resolveAssetUrl", () => {
  it("leaves an org asset alone when there is a session", () => {
    expect(resolveAssetUrl("/api/assets/public/models/a.glb", null)).toBe("/api/assets/public/models/a.glb");
  });

  it("rewrites an org asset onto the token route on a shared page", () => {
    expect(resolveAssetUrl("/api/assets/public/models/a.glb", TOKEN)).toBe(
      `${API_BASE}/public/views/${TOKEN}/assets/public/models/a.glb`
    );
  });

  it("keeps the whole path, not just the last segment", () => {
    expect(resolveAssetUrl("/api/assets/public/a/b/c.stl", TOKEN)).toContain("/assets/public/a/b/c.stl");
  });

  it("encodes the token rather than splicing it in raw", () => {
    // The token reaches this from the URL bar; it becomes a path segment here.
    expect(resolveAssetUrl("/api/assets/public/a.stl", "a/b")).toContain("/public/views/a%2Fb/assets/");
  });

  it("leaves a static path and an external URL untouched in both contexts", () => {
    for (const token of [null, TOKEN]) {
      expect(resolveAssetUrl("/demo/unit.svg", token)).toBe("/demo/unit.svg");
      expect(resolveAssetUrl("https://example.com/a.stl", token)).toBe("https://example.com/a.stl");
    }
  });

  it("does not rewrite an already-public URL a second time", () => {
    const already = `${API_BASE}/public/views/${TOKEN}/assets/public/a.stl`;
    expect(resolveAssetUrl(already, TOKEN)).toBe(already);
  });
});

describe("isApiAsset", () => {
  it("is true for the org-scoped route, which needs the session's headers", () => {
    expect(isApiAsset("/api/assets/public/a.stl")).toBe(true);
  });

  it("is false for the public share route, which carries its own credential", () => {
    expect(isApiAsset("/api/public/views/t/assets/public/a.stl")).toBe(false);
  });

  it("is false for anything that is not an API path", () => {
    expect(isApiAsset("/demo/unit.svg")).toBe(false);
    expect(isApiAsset("https://example.com/a.stl")).toBe(false);
  });
});
