import { describe, expect, it } from "vitest";

import type { EntityField } from "@/lib/api/entities";
import { coerceValue, makeRef, objectToRows, parseRef, rowsToObject } from "./recordFields";

function field(partial: Partial<EntityField> & Pick<EntityField, "slug" | "field_type">): EntityField {
  return {
    id: partial.slug,
    name: partial.slug,
    picklist_options: null,
    is_required: false,
    is_unique: false,
    default_value: null,
    order: 0,
    ...partial,
  };
}

describe("objectToRows", () => {
  it("expands a flat object into key/value rows", () => {
    expect(objectToRows({ status: "closed", amount: 10, done: true })).toEqual([
      { key: "status", value: "closed" },
      { key: "amount", value: "10" },
      { key: "done", value: "true" },
    ]);
  });

  it("treats null/undefined and empty strings as an empty record", () => {
    expect(objectToRows(null)).toEqual([]);
    expect(objectToRows(undefined)).toEqual([]);
    expect(objectToRows("")).toEqual([]);
    expect(objectToRows("   ")).toEqual([]);
  });

  it("parses a JSON string value", () => {
    expect(objectToRows('{"status":"open"}')).toEqual([{ key: "status", value: "open" }]);
  });

  it("returns null for values it can't represent as flat rows", () => {
    expect(objectToRows("not json")).toBeNull();
    expect(objectToRows("[1,2,3]")).toBeNull();
    expect(objectToRows({ nested: { a: 1 } })).toBeNull();
    expect(objectToRows({ list: [1, 2] })).toBeNull();
  });
});

describe("trigger-field references", () => {
  it("parseRef recognises an after.<slug> envelope and nothing else", () => {
    expect(parseRef({ $ref: "after.first_name" })).toBe("first_name");
    expect(parseRef(makeRef("email"))).toBe("email");
    expect(parseRef({ $ref: "before.x" })).toBeNull(); // only the after. namespace is surfaced
    expect(parseRef({ $ref: "after.x", extra: 1 })).toBeNull(); // must be the sole key
    expect(parseRef("literal")).toBeNull();
    expect(parseRef({ nested: { a: 1 } })).toBeNull();
  });

  it("objectToRows turns a $ref value into a ref row, not a literal", () => {
    expect(objectToRows({ first_name: { $ref: "after.first_name" }, last_name: "Blair" })).toEqual([
      { key: "first_name", value: "", ref: "first_name" },
      { key: "last_name", value: "Blair" },
    ]);
  });

  it("rowsToObject serialises a ref row back to the envelope", () => {
    const rows = [
      { key: "first_name", value: "", ref: "first_name" },
      { key: "last_name", value: "Blair" },
    ];
    expect(rowsToObject(rows, undefined)).toEqual({
      first_name: { $ref: "after.first_name" },
      last_name: "Blair",
    });
  });

  it("round-trips a mixed literal + ref record", () => {
    const original = { first_name: { $ref: "after.first_name" }, note: "hi" };
    const rows = objectToRows(original);
    expect(rows).not.toBeNull();
    expect(rowsToObject(rows!, undefined)).toEqual(original);
  });
});

describe("coerceValue", () => {
  it("coerces by entity field type", () => {
    expect(coerceValue(field({ slug: "n", field_type: "integer" }), "42")).toBe(42);
    expect(coerceValue(field({ slug: "n", field_type: "numeric" }), "1.5")).toBe(1.5);
    expect(coerceValue(field({ slug: "b", field_type: "boolean" }), "true")).toBe(true);
    expect(coerceValue(field({ slug: "b", field_type: "boolean" }), "false")).toBe(false);
    expect(coerceValue(field({ slug: "j", field_type: "json" }), '{"a":1}')).toEqual({ a: 1 });
  });

  it("keeps text fields and unknown fields as strings", () => {
    expect(coerceValue(field({ slug: "s", field_type: "text" }), "hello")).toBe("hello");
    expect(coerceValue(undefined, "hello")).toBe("hello");
  });

  it("leaves invalid numbers/json untouched rather than emitting NaN", () => {
    expect(coerceValue(field({ slug: "n", field_type: "integer" }), "abc")).toBe("abc");
    expect(coerceValue(field({ slug: "j", field_type: "json" }), "{bad")).toBe("{bad");
  });
});

describe("rowsToObject", () => {
  const fields = [
    field({ slug: "amount", field_type: "numeric" }),
    field({ slug: "status", field_type: "text" }),
  ];

  it("builds a typed object and skips blank keys", () => {
    const rows = [
      { key: "amount", value: "12.5" },
      { key: "status", value: "closed" },
      { key: "", value: "ignored" },
    ];
    expect(rowsToObject(rows, fields)).toEqual({ amount: 12.5, status: "closed" });
  });

  it("keeps free-form keys as strings when no field matches", () => {
    expect(rowsToObject([{ key: "custom", value: "x" }], fields)).toEqual({ custom: "x" });
  });

  it("round-trips through objectToRows for scalar records", () => {
    const original = { amount: 3, status: "open" };
    const rows = objectToRows(original);
    expect(rows).not.toBeNull();
    expect(rowsToObject(rows!, fields)).toEqual(original);
  });
});
