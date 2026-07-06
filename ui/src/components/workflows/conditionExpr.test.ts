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

  describe("coerce (numeric-looking strings)", () => {
    it("keeps look-alike numeric strings intact for non-numeric fields", () => {
      // "01234" / "1e3" would be corrupted by a blind Number() cast.
      expect(compileRows([{ field: "after.code", op: "==", value: "01234" }])).toEqual({
        "==": [{ var: "after.code" }, "01234"],
      });
      expect(compileRows([{ field: "after.code", op: "==", value: "1e3" }])).toEqual({
        "==": [{ var: "after.code" }, "1e3"],
      });
    });

    it("coerces values that round-trip exactly", () => {
      expect(compileRows([{ field: "after.n", op: "==", value: "12.9" }])).toEqual({
        "==": [{ var: "after.n" }, 12.9],
      });
      expect(compileRows([{ field: "after.n", op: "==", value: "100" }])).toEqual({
        "==": [{ var: "after.n" }, 100],
      });
    });

    it("coerces to number when the field is declared numeric via the resolver", () => {
      const isNumeric = (path: string) => path === "after.code";
      expect(compileRows([{ field: "after.code", op: "==", value: "01234" }], isNumeric)).toEqual({
        "==": [{ var: "after.code" }, 1234],
      });
    });
  });

  describe("'in' operator edge cases", () => {
    it("treats an empty/whitespace value as an empty list, not ['']", () => {
      expect(compileRows([{ field: "after.s", op: "in", value: "" }])).toEqual({
        in: [{ var: "after.s" }, []],
      });
      expect(compileRows([{ field: "after.s", op: "in", value: "  " }])).toEqual({
        in: [{ var: "after.s" }, []],
      });
    });

    it("drops blank entries between commas", () => {
      expect(compileRows([{ field: "after.s", op: "in", value: "a, , b" }])).toEqual({
        in: [{ var: "after.s" }, ["a", "b"]],
      });
    });

    it("falls back to the raw editor for lists whose elements contain commas", () => {
      expect(parseExpr({ in: [{ var: "after.s" }, ["a,b", "c"]] })).toBeNull();
    });
  });

  describe("parseClause scalar guard", () => {
    it("returns null when the right side is a var (not a scalar)", () => {
      expect(parseExpr({ "==": [{ var: "a" }, { var: "b" }] })).toBeNull();
    });

    it("returns null when the right side is a list or object", () => {
      expect(parseExpr({ ">": [{ var: "a" }, [1, 2]] })).toBeNull();
      expect(parseExpr({ ">": [{ var: "a" }, { nested: 1 }] })).toBeNull();
    });
  });
});
