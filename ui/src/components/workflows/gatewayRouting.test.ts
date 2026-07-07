import { describe, expect, it } from "vitest";

import { routingMode, toCasesMode, toConditionMode } from "./gatewayRouting";

describe("gatewayRouting", () => {
  describe("routingMode", () => {
    it("is 'cases' only when a non-empty cases array is present", () => {
      expect(routingMode({ cases: [{ handle: "c1", expr: null }] })).toBe("cases");
      expect(routingMode({ cases: [] })).toBe("condition");
      expect(routingMode({ expr: { "==": [1, 1] } })).toBe("condition");
      expect(routingMode({})).toBe("condition");
    });
  });

  describe("toConditionMode", () => {
    it("drops cases and preserves the rest immutably", () => {
      const data = { gateway_type: "exclusive", expr: { "==": [1, 1] }, cases: [{ handle: "c1" }] };
      const next = toConditionMode(data);
      expect(next).toEqual({ gateway_type: "exclusive", expr: { "==": [1, 1] } });
      expect(next).not.toBe(data);
      expect(data.cases).toBeDefined(); // original untouched
    });
  });

  describe("toCasesMode", () => {
    it("drops expr and ensures a cases array, preserving existing cases", () => {
      const existing = [{ handle: "c1", label: "A", expr: null }];
      const data = { gateway_type: "exclusive", expr: { "==": [1, 1] }, cases: existing };
      const next = toCasesMode(data);
      expect(next).toEqual({ gateway_type: "exclusive", cases: existing });
      expect(next).not.toHaveProperty("expr");
    });

    it("seeds an empty cases array when none exists", () => {
      expect(toCasesMode({ gateway_type: "exclusive", expr: {} })).toEqual({
        gateway_type: "exclusive",
        cases: [],
      });
    });
  });
});
