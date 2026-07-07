import { describe, expect, it } from "vitest";

import {
  asJsonLogic,
  emptyRule,
  normalizeHitPolicy,
  readDecisionTable,
  writeDecisionTable,
  type DecisionTable,
} from "./decisionTable";

describe("decisionTable", () => {
  describe("normalizeHitPolicy", () => {
    it("keeps 'collect' and defaults everything else to 'first'", () => {
      expect(normalizeHitPolicy("collect")).toBe("collect");
      expect(normalizeHitPolicy("first")).toBe("first");
      expect(normalizeHitPolicy("nonsense")).toBe("first");
      expect(normalizeHitPolicy(undefined)).toBe("first");
    });
  });

  describe("asJsonLogic", () => {
    it("passes objects through and maps everything else to null", () => {
      expect(asJsonLogic({ "==": [1, 1] })).toEqual({ "==": [1, 1] });
      expect(asJsonLogic(null)).toBeNull();
      expect(asJsonLogic(undefined)).toBeNull();
      expect(asJsonLogic([1, 2])).toBeNull();
      expect(asJsonLogic("x")).toBeNull();
    });
  });

  describe("readDecisionTable", () => {
    it("degrades an absent table to first/empty", () => {
      expect(readDecisionTable({})).toEqual({ hit_policy: "first", rules: [] });
    });

    it("normalizes rules, coercing bad when/output to null/{}", () => {
      const table = readDecisionTable({
        decision_table: {
          hit_policy: "collect",
          rules: [
            { when: { "==": [{ var: "after.status" }, "open"] }, output: { priority: "high" } },
            { when: "garbage", output: null },
            {},
          ],
        },
      });
      expect(table.hit_policy).toBe("collect");
      expect(table.rules).toEqual([
        { when: { "==": [{ var: "after.status" }, "open"] }, output: { priority: "high" } },
        { when: null, output: {} },
        { when: null, output: {} },
      ]);
    });

    it("ignores a non-array rules value", () => {
      expect(readDecisionTable({ decision_table: { rules: "nope" } }).rules).toEqual([]);
    });
  });

  describe("writeDecisionTable round-trips through readDecisionTable", () => {
    it("preserves policy, conditions and outputs", () => {
      const table: DecisionTable = {
        hit_policy: "collect",
        rules: [
          { when: { ">": [{ var: "after.amount" }, 100] }, output: { tier: "gold", flagged: true } },
          { ...emptyRule(), output: { tier: "standard" } },
        ],
      };
      const stored = writeDecisionTable(table);
      expect(readDecisionTable({ decision_table: stored })).toEqual(table);
    });

    it("drops editor-only keys layered onto a rule", () => {
      const stored = writeDecisionTable({
        hit_policy: "first",
        rules: [{ when: null, output: {}, ...({ id: "rule_x" } as object) } as DecisionTable["rules"][number]],
      });
      expect(stored.rules).toEqual([{ when: null, output: {} }]);
    });
  });

  it("emptyRule matches unconditionally and sets nothing", () => {
    expect(emptyRule()).toEqual({ when: null, output: {} });
  });
});
