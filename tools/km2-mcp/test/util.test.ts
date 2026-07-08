import { test } from "node:test";
import assert from "node:assert/strict";
import { pruneUndefined, uuid } from "../src/tools/util.js";

test("pruneUndefined drops undefined but keeps null/false/0/empty-string", () => {
  const input = { a: 1, b: undefined, c: null, d: false, e: 0, f: "" };
  const out = pruneUndefined(input);
  assert.deepEqual(out, { a: 1, c: null, d: false, e: 0, f: "" });
  assert.equal("b" in out, false);
});

test("uuid schema validates real UUIDs and rejects junk", () => {
  assert.equal(uuid.safeParse("3f2504e0-4f89-41d3-9a0c-0305e82c3301").success, true);
  assert.equal(uuid.safeParse("not-a-uuid").success, false);
  assert.equal(uuid.safeParse("").success, false);
});
