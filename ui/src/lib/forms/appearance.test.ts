import { describe, expect, it } from "vitest";

import { appearanceProps } from "./appearance";

describe("appearanceProps", () => {
  it("returns nothing for an absent appearance block", () => {
    expect(appearanceProps(null)).toEqual({});
    expect(appearanceProps(undefined)).toEqual({});
  });

  it("maps colors to custom properties", () => {
    const props = appearanceProps({ colors: { primary: "#233f7a", border: "#9c9da0" } });
    expect(props.style).toEqual({
      "--color-primary": "#233f7a",
      "--color-border": "#9c9da0",
    });
  });

  it("maps treatments to data attributes", () => {
    const props = appearanceProps({
      surface: "glass",
      button_finish: "gradient",
      texture: "diamond",
      heading_case: "capitalize",
    });
    expect(props["data-surface"]).toBe("glass");
    expect(props["data-button-finish"]).toBe("gradient");
    expect(props["data-texture"]).toBe("diamond");
    expect(props["data-heading-case"]).toBe("capitalize");
  });

  it("emits the radius as a length", () => {
    expect(appearanceProps({ radius_px: 15 }).style).toEqual({ "--view-radius": "15px" });
  });

  it("clamps an out-of-range radius rather than dropping it", () => {
    expect(appearanceProps({ radius_px: 999 }).style).toEqual({ "--view-radius": "48px" });
    expect(appearanceProps({ radius_px: -5 }).style).toEqual({ "--view-radius": "0px" });
  });

  describe("hostile values", () => {
    // These are the last line of defence: the server rejects them on write, but a
    // view definition can also arrive from an import bundle or a pre-validator row.
    it.each([
      "red",
      "#12345",
      "rgb(0,0,0)",
      "#000; background: url(https://evil.example/beacon)",
      "#000000}html{display:none",
      "url(javascript:alert(1))",
      "",
    ])("drops the non-hex color %j", (color) => {
      expect(appearanceProps({ colors: { primary: color } })).toEqual({});
    });

    it("drops a token name that is not a plain custom-property word", () => {
      expect(appearanceProps({ colors: { "primary: red; --x": "#000000" } })).toEqual({});
    });

    it("keeps the valid colors when one entry is rejected", () => {
      const props = appearanceProps({ colors: { primary: "#233f7a", border: "javascript:x" } });
      expect(props.style).toEqual({ "--color-primary": "#233f7a" });
    });
  });

  describe("org accent", () => {
    it("applies a valid accent as the primary color", () => {
      expect(appearanceProps(null, "#ff0000").style).toEqual({ "--color-primary": "#ff0000" });
    });

    it("ignores an invalid accent", () => {
      expect(appearanceProps(null, "nonsense")).toEqual({});
    });

    it("lets the more specific appearance color win over the accent", () => {
      const props = appearanceProps({ colors: { primary: "#233f7a" } }, "#ff0000");
      expect(props.style).toEqual({ "--color-primary": "#233f7a" });
    });
  });
});
