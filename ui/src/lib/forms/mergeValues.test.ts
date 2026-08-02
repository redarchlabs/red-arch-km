import { describe, expect, it } from "vitest";

import { mergeServerValues, sameValue } from "./mergeValues";

describe("sameValue", () => {
  it("compares scalars by identity", () => {
    expect(sameValue(1, 1)).toBe(true);
    expect(sameValue("a", "a")).toBe(true);
    expect(sameValue(null, null)).toBe(true);
    expect(sameValue(1, "1")).toBe(false);
    expect(sameValue(null, undefined)).toBe(false);
  });

  it("compares objects and arrays structurally", () => {
    expect(sameValue({ a: 1 }, { a: 1 })).toBe(true);
    expect(sameValue([1, { b: 2 }], [1, { b: 2 }])).toBe(true);
    expect(sameValue({ a: 1 }, { a: 2 })).toBe(false);
  });

  it("treats a cyclic value as changed rather than throwing", () => {
    const cyclic: Record<string, unknown> = {};
    cyclic.self = cyclic;
    expect(sameValue(cyclic, { self: {} })).toBe(false);
  });
});

describe("mergeServerValues", () => {
  const none = new Set<string>();

  it("adopts changed server values", () => {
    const out = mergeServerValues({ hull: 100, phase: "Cruise" }, { hull: 60, phase: "Crisis" }, none);
    expect(out).toEqual({ hull: 60, phase: "Crisis" });
  });

  it("returns the same reference when nothing changed", () => {
    const current = { hull: 100, systems: { shields: 80 } };
    const out = mergeServerValues(current, { hull: 100, systems: { shields: 80 } }, none);
    expect(out).toBe(current); // no re-render on a quiet poll
  });

  it("never overwrites a value the viewer is editing", () => {
    const out = mergeServerValues(
      { crew_name: "Zoe typing…", hull: 100 },
      { crew_name: "Zoe", hull: 60 },
      new Set(["crew_name"]),
    );
    expect(out).toEqual({ crew_name: "Zoe typing…", hull: 60 });
  });

  it("leaves local-only keys alone", () => {
    // A standalone `input`'s value lives only in the browser; the server render has
    // no such key and must not blank it.
    const out = mergeServerValues({ answer_choice: "B", hull: 100 }, { hull: 60 }, none);
    expect(out).toEqual({ answer_choice: "B", hull: 60 });
  });

  it("passes the current values through when there is no server payload", () => {
    const current = { hull: 100 };
    expect(mergeServerValues(current, null, none)).toBe(current);
    expect(mergeServerValues(current, undefined, none)).toBe(current);
  });
});
