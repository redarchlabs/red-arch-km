import { describe, expect, it } from "vitest";

import { isTheme, resolveInitialTheme, THEMES } from "./theme";

describe("isTheme", () => {
  it("accepts every known theme", () => {
    for (const t of THEMES) {
      expect(isTheme(t)).toBe(true);
    }
  });

  it("rejects unknown values", () => {
    expect(isTheme("solarized")).toBe(false);
    expect(isTheme(null)).toBe(false);
    expect(isTheme(42)).toBe(false);
  });
});

describe("resolveInitialTheme", () => {
  it("prefers a valid stored theme", () => {
    expect(resolveInitialTheme("redarch", true)).toBe("redarch");
    expect(resolveInitialTheme("light", true)).toBe("light");
  });

  it("falls back to the OS preference when nothing valid is stored", () => {
    expect(resolveInitialTheme(null, true)).toBe("dark");
    expect(resolveInitialTheme(null, false)).toBe("light");
    expect(resolveInitialTheme("garbage", true)).toBe("dark");
  });
});
