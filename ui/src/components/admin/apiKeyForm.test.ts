import { describe, expect, it } from "vitest";

import { describeMintError, droppedScopes, effectiveScopes } from "./apiKeyForm";

const SCOPED_OK = new Set(["search:read", "knowledge:read"]);

describe("effective scopes", () => {
  const selected = new Set(["search:read", "records:read", "knowledge:read"]);

  it("keeps every selected scope for an organization-wide key", () => {
    expect([...effectiveScopes(selected, false, SCOPED_OK)].sort()).toEqual([
      "knowledge:read",
      "records:read",
      "search:read",
    ]);
    expect(droppedScopes(selected, false, SCOPED_OK)).toEqual([]);
  });

  it("drops scopes a scoped key cannot hold, without losing the selection", () => {
    expect([...effectiveScopes(selected, true, SCOPED_OK)].sort()).toEqual(["knowledge:read", "search:read"]);
    expect(droppedScopes(selected, true, SCOPED_OK)).toEqual(["records:read"]);
    // The selection itself is untouched: clearing the assignments brings it back.
    expect(selected.has("records:read")).toBe(true);
  });
});

describe("describeMintError", () => {
  const names = new Map([
    ["7b0c3f0e-1111-4c2d-9a8b-000000000001", "Projects.Weekly"],
    ["7b0c3f0e-1111-4c2d-9a8b-000000000002", "Finance"],
  ]);

  it("replaces known ids with their folder paths or names", () => {
    const msg =
      "Unknown folder id(s) in this organization: 7b0c3f0e-1111-4c2d-9a8b-000000000001, 7B0C3F0E-1111-4C2D-9A8B-000000000002";
    expect(describeMintError(msg, names)).toBe("Unknown folder id(s) in this organization: Projects.Weekly, Finance");
  });

  it("leaves unknown ids alone", () => {
    const msg = "Unknown role id(s) in this organization: 00000000-0000-4000-8000-000000000000";
    expect(describeMintError(msg, names)).toBe(msg);
  });
});
