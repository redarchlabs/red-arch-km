import { describe, expect, it } from "vitest";

import { compileRows, describeExpr, parseExpr, type ConditionRow } from "./conditionExpr";

describe("conditionExpr", () => {
  it("compiles a single row to JsonLogic", () => {
    const rows: ConditionRow[] = [{ field: "after.status", op: "==", value: "closed" }];
    expect(compileRows(rows)).toEqual({ "==": [{ var: "after.status" }, "closed"] });
  });

  it("coerces numeric and boolean values", () => {
    expect(compileRows([{ field: "after.n", op: ">", value: "10" }])).toEqual({
      ">": [{ var: "after.n" }, 10],
    });
    expect(compileRows([{ field: "after.b", op: "==", value: "true" }])).toEqual({
      "==": [{ var: "after.b" }, true],
    });
  });

  it("AND-combines multiple rows", () => {
    const expr = compileRows([
      { field: "after.a", op: "==", value: "x" },
      { field: "after.b", op: ">", value: "1" },
    ]);
    expect(expr).toEqual({
      and: [{ "==": [{ var: "after.a" }, "x"] }, { ">": [{ var: "after.b" }, 1] }],
    });
  });

  it("compiles 'in' with a comma list", () => {
    expect(compileRows([{ field: "after.s", op: "in", value: "open, closed" }])).toEqual({
      in: [{ var: "after.s" }, ["open", "closed"]],
    });
  });

  it("returns null for empty rows (always true)", () => {
    expect(compileRows([])).toBeNull();
    expect(compileRows([{ field: "", op: "==", value: "x" }])).toBeNull();
  });

  it("round-trips through parseExpr", () => {
    const rows: ConditionRow[] = [
      { field: "after.a", op: "==", value: "x" },
      { field: "after.n", op: ">=", value: "5" },
    ];
    const parsed = parseExpr(compileRows(rows));
    expect(parsed).toEqual([
      { field: "after.a", op: "==", value: "x" },
      { field: "after.n", op: ">=", value: "5" },
    ]);
  });

  it("returns null from parseExpr for un-representable expressions", () => {
    expect(parseExpr({ or: [{ "==": [{ var: "a" }, 1] }] })).toBeNull();
  });

  it("describes an expression for node labels", () => {
    expect(describeExpr({ "==": [{ var: "after.status" }, "closed"] })).toBe("after.status == closed");
    expect(describeExpr(null)).toBe("");
    expect(describeExpr({ or: [] })).toBe("custom rule");
  });
});
