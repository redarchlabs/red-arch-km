import { describe, expect, it } from "vitest";

import { evaluate } from "./jsonLogic";

describe("jsonLogic evaluator (parity with form_expression.py)", () => {
  it("passes literals through", () => {
    expect(evaluate(42, {})).toBe(42);
    expect(evaluate("hi", {})).toBe("hi");
    expect(evaluate(true, {})).toBe(true);
    expect(evaluate(null, {})).toBe(null);
  });

  it("resolves vars incl. missing + dotted paths", () => {
    expect(evaluate({ var: "name" }, { name: "Ada" })).toBe("Ada");
    expect(evaluate({ var: "missing" }, { name: "Ada" })).toBe(null);
    expect(evaluate({ var: "a.b" }, { a: { b: 7 } })).toBe(7);
  });

  it("does arithmetic incl. div-by-zero → null", () => {
    expect(evaluate({ "+": [{ var: "a" }, { "*": [{ var: "b" }, 2] }] }, { a: 10, b: 5 })).toBe(20);
    expect(evaluate({ "-": [10, 3, 2] }, {})).toBe(5);
    expect(evaluate({ "/": [1, 0] }, {})).toBe(null);
  });

  it("concatenates strings, dropping nulls", () => {
    expect(evaluate({ cat: [{ var: "f" }, " ", { var: "l" }] }, { f: "Ada", l: "L" })).toBe("Ada L");
    expect(evaluate({ cat: ["x", { var: "missing" }] }, {})).toBe("x");
  });

  it("handles conditionals + comparisons", () => {
    expect(evaluate({ if: [{ ">": [{ var: "n" }, 3] }, "big", "small"] }, { n: 5 })).toBe("big");
    expect(evaluate({ if: [{ ">": [{ var: "n" }, 3] }, "big", "small"] }, { n: 1 })).toBe("small");
    expect(evaluate({ "==": [{ var: "n" }, 5] }, { n: 5 })).toBe(true);
    expect(evaluate({ "<": ["9", 10] }, {})).toBe(true);
  });

  it("handles and/or/not", () => {
    expect(evaluate({ and: [true, true] }, {})).toBe(true);
    expect(evaluate({ and: [true, false] }, {})).toBe(false);
    expect(evaluate({ or: [false, "fallback"] }, {})).toBe("fallback");
    expect(evaluate({ "!": [false] }, {})).toBe(true);
  });

  it("today/now are ISO", () => {
    const today = evaluate({ today: [] }, {}) as string;
    expect(today).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(evaluate({ now: [] }, {}) as string).toContain("T");
  });

  it.each([
    ["2026-07-07", 30, "day", "2026-08-06"],
    ["2026-07-07", 2, "week", "2026-07-21"],
    ["2026-01-31", 1, "month", "2026-02-28"],
    ["2026-07-07", 1, "year", "2027-07-07"],
  ])("date_add(%s, %i, %s) = %s", (base, amount, unit, expected) => {
    expect(evaluate({ date_add: [base, amount, unit] }, {})).toBe(expected);
  });

  it("date_diff in days (signed)", () => {
    expect(evaluate({ date_diff: ["2026-08-01", "2026-07-07"] }, {})).toBe(25);
    expect(evaluate({ date_diff: ["2026-07-07", "2026-08-01"] }, {})).toBe(-25);
  });

  it("degrades bad formulas to null", () => {
    expect(evaluate({ unknown_op: [1, 2] }, {})).toBe(null);
    expect(evaluate({ date_add: ["not-a-date", 1, "day"] }, {})).toBe(null);
  });
});
