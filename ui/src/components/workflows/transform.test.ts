import { describe, expect, it } from "vitest";

import {
  exprToText,
  parseExprText,
  readTransform,
  rowsToTransform,
  transformToRows,
  type TransformRow,
} from "./transform";

describe("transform", () => {
  describe("parseExprText", () => {
    it("parses JsonLogic objects, numbers and booleans", () => {
      expect(parseExprText('{ "var": "after.x" }')).toEqual({ var: "after.x" });
      expect(parseExprText("42")).toBe(42);
      expect(parseExprText("true")).toBe(true);
    });

    it("keeps non-JSON text as a string literal", () => {
      expect(parseExprText("after.name")).toBe("after.name");
      expect(parseExprText("hello world")).toBe("hello world");
    });

    it("treats empty/whitespace as an empty string", () => {
      expect(parseExprText("")).toBe("");
      expect(parseExprText("   ")).toBe("");
    });
  });

  describe("exprToText / parseExprText round-trip", () => {
    const cases: { label: string; value: unknown }[] = [
      { label: "jsonlogic object", value: { var: "after.status" } },
      { label: "number", value: 42 },
      { label: "float", value: 12.5 },
      { label: "boolean", value: true },
      { label: "plain string", value: "hello" },
      { label: "numeric-looking string", value: "42" },
      { label: "boolean-looking string", value: "true" },
      { label: "quoted-string string", value: '"hello"' },
      { label: "empty string", value: "" },
      { label: "array", value: [1, 2, 3] },
    ];
    it.each(cases)("round-trips a $label", ({ value }) => {
      expect(parseExprText(exprToText(value))).toEqual(value);
    });
  });

  describe("transformToRows / rowsToTransform", () => {
    it("decomposes a transform map into ordered rows", () => {
      expect(transformToRows({ total: { "+": [1, 2] }, note: "hi" })).toEqual([
        { key: "total", expr: '{"+":[1,2]}' },
        { key: "note", expr: "hi" },
      ]);
    });

    it("rebuilds a transform map, parsing each cell", () => {
      const rows: TransformRow[] = [
        { key: "total", expr: '{ "+": [1, 2] }' },
        { key: "flag", expr: "true" },
        { key: "label", expr: "done" },
      ];
      expect(rowsToTransform(rows)).toEqual({ total: { "+": [1, 2] }, flag: true, label: "done" });
    });

    it("drops blank-keyed rows and trims keys", () => {
      expect(rowsToTransform([{ key: "  ", expr: "1" }, { key: " x ", expr: "2" }])).toEqual({ x: 2 });
    });

    it("lets a later row win a duplicate key", () => {
      expect(rowsToTransform([{ key: "x", expr: "1" }, { key: "x", expr: "2" }])).toEqual({ x: 2 });
    });

    it("round-trips a transform map through rows", () => {
      const map = { total: { "+": [1, 2] }, flag: true, code: "01234", note: "hi" };
      expect(rowsToTransform(transformToRows(map))).toEqual(map);
    });
  });

  describe("readTransform", () => {
    it("returns the map when present and {} otherwise", () => {
      expect(readTransform({ transform: { a: 1 } })).toEqual({ a: 1 });
      expect(readTransform({})).toEqual({});
      expect(readTransform({ transform: [1, 2] })).toEqual({});
      expect(readTransform({ transform: "nope" })).toEqual({});
    });
  });
});
