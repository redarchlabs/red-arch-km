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

  // These ops exist in the SERVER-side evaluator (services/workflow/jsonlogic.py).
  // A `visible_when` written against that vocabulary is evaluated HERE, so a gap
  // silently hid whole sections of a view (an unknown op throws -> null -> falsy).
  describe("parity with the server evaluator", () => {
    it("!! casts to truthy", () => {
      expect(evaluate({ "!!": [{ var: "puzzle" }] }, { puzzle: "an-id" })).toBe(true);
      expect(evaluate({ "!!": [{ var: "puzzle" }] }, { puzzle: null })).toBe(false);
      expect(evaluate({ "!!": [{ var: "puzzle" }] }, {})).toBe(false);
    });

    it("=== and !== compare strictly, without numeric coercion", () => {
      expect(evaluate({ "===": [3, 3] }, {})).toBe(true);
      expect(evaluate({ "===": ["3", 3] }, {})).toBe(false); // `==` would coerce
      expect(evaluate({ "==": ["3", 3] }, {})).toBe(true);
      expect(evaluate({ "!==": [{ var: "status" }, "complete"] }, { status: "active" })).toBe(true);
      expect(evaluate({ "!==": [{ var: "status" }, "complete"] }, { status: "complete" })).toBe(false);
    });

    it("gates the crew station's answer buttons on a live challenge", () => {
      // The exact expression the Crew Station view uses: show the answer buttons
      // only when a challenge is loaded, unanswered, and the mission is running.
      const showButtons = {
        and: [
          { "!!": [{ var: "current_puzzle" }] },
          { "==": [{ var: "last_result" }, "none"] },
          { "!==": [{ var: "status" }, "complete"] },
        ],
      };
      const live = { current_puzzle: "p-1", last_result: "none", status: "active" };
      expect(Boolean(evaluate(showButtons, live))).toBe(true);
      expect(Boolean(evaluate(showButtons, { ...live, current_puzzle: null }))).toBe(false);
      expect(Boolean(evaluate(showButtons, { ...live, last_result: "correct" }))).toBe(false);
      expect(Boolean(evaluate(showButtons, { ...live, status: "complete" }))).toBe(false);
    });
    it("in matches a list element or a substring", () => {
      expect(evaluate({ in: ["b", ["a", "b"]] }, {})).toBe(true);
      expect(evaluate({ in: ["z", ["a", "b"]] }, {})).toBe(false);
      expect(evaluate({ in: ["ell", "hello"] }, {})).toBe(true);
      expect(evaluate({ in: ["x", null] }, {})).toBe(false);
    });
  });
});

describe("puzzle pad outcome expressions", () => {
  // The crew station maps a pad's outcome into workflow inputs with these exact
  // expressions. `graded` has to tell "the pad graded this and says no" apart from
  // "the pad was never told the answer" — a distinction `!` cannot make, since
  // false and null are both falsy.
  const graded = { "!==": [{ var: "solved" }, null] };

  it("reports graded for a pad that returned a verdict, either way", () => {
    expect(evaluate(graded, { solved: true })).toBe(true);
    expect(evaluate(graded, { solved: false })).toBe(true);
  });

  it("reports NOT graded when the pad was never told the answer", () => {
    expect(evaluate(graded, { solved: null })).toBe(false);
  });

  it("lets the outcome shadow a same-named record field", () => {
    // `solved` is also a mission_run field (puzzles solved so far). The renderer
    // spreads the outcome last precisely so the pad's verdict wins here.
    const context = { ...{ solved: 7 }, ...{ solved: null, answer: "C" } };
    expect(evaluate(graded, context)).toBe(false);
    expect(evaluate({ var: "answer" }, context)).toBe("C");
  });
});
