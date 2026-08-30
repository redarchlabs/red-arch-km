import { describe, expect, it } from "vitest";

import { isTheme, resolveInitialTheme, THEME_LABELS, THEMES } from "./theme";

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

  it("keeps the console theme over the OS preference", () => {
    // A kiosk pinned to console must not fall back to dark because the machine
    // it is plugged into prefers dark — the pin is the whole point.
    expect(resolveInitialTheme("console", true)).toBe("console");
    expect(resolveInitialTheme("console", false)).toBe("console");
  });
});

describe("THEME_LABELS", () => {
  it("labels every theme", () => {
    // The picker renders from THEMES; a theme with no label ships as "undefined".
    for (const theme of THEMES) {
      expect(THEME_LABELS[theme]).toBeTruthy();
    }
  });
});
