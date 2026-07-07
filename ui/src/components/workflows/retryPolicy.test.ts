import { describe, expect, it } from "vitest";

import {
  applyContinueOnError,
  applyRetry,
  DEFAULT_RETRY,
  DELAY_CAP_SECONDS,
  MAX_ATTEMPTS_CAP,
  normalizeRetry,
  readRetry,
} from "./retryPolicy";

describe("normalizeRetry", () => {
  it("fills defaults from an empty/garbage input", () => {
    expect(normalizeRetry({})).toEqual(DEFAULT_RETRY);
    expect(normalizeRetry(undefined)).toEqual(DEFAULT_RETRY);
    expect(normalizeRetry(null)).toEqual(DEFAULT_RETRY);
    expect(normalizeRetry("nope")).toEqual(DEFAULT_RETRY);
  });

  it("coerces stringy numbers from number inputs", () => {
    expect(normalizeRetry({ max_attempts: "5", base_delay_seconds: "2", max_delay_seconds: "60" })).toEqual({
      max_attempts: 5,
      base_delay_seconds: 2,
      max_delay_seconds: 60,
    });
  });

  it("clamps max_attempts to [1, cap] and floors it", () => {
    expect(normalizeRetry({ max_attempts: 0 }).max_attempts).toBe(1);
    expect(normalizeRetry({ max_attempts: 999 }).max_attempts).toBe(MAX_ATTEMPTS_CAP);
    expect(normalizeRetry({ max_attempts: 3.9 }).max_attempts).toBe(3);
  });

  it("clamps delays to [0, cap]", () => {
    expect(normalizeRetry({ base_delay_seconds: -5 }).base_delay_seconds).toBe(0);
    expect(normalizeRetry({ max_delay_seconds: 10 ** 9 }).max_delay_seconds).toBe(DELAY_CAP_SECONDS);
  });

  it("never lets max_delay fall below base_delay", () => {
    const p = normalizeRetry({ base_delay_seconds: 120, max_delay_seconds: 30 });
    expect(p.base_delay_seconds).toBe(120);
    expect(p.max_delay_seconds).toBe(120);
  });

  it("falls back to the default for a non-numeric field, keeping others", () => {
    expect(normalizeRetry({ max_attempts: "abc", base_delay_seconds: 7 })).toEqual({
      max_attempts: DEFAULT_RETRY.max_attempts,
      base_delay_seconds: 7,
      max_delay_seconds: DEFAULT_RETRY.max_delay_seconds,
    });
  });
});

describe("readRetry", () => {
  it("returns null when retry is absent, empty, or not an object", () => {
    expect(readRetry({})).toBeNull();
    expect(readRetry({ retry: {} })).toBeNull();
    expect(readRetry({ retry: null })).toBeNull();
    expect(readRetry({ retry: [] })).toBeNull();
    expect(readRetry({ retry: "x" })).toBeNull();
  });

  it("returns a normalized policy when retry is present", () => {
    expect(readRetry({ retry: { max_attempts: 4 } })).toEqual({
      max_attempts: 4,
      base_delay_seconds: DEFAULT_RETRY.base_delay_seconds,
      max_delay_seconds: DEFAULT_RETRY.max_delay_seconds,
    });
  });
});

describe("applyRetry", () => {
  it("writes a normalized policy under the retry key", () => {
    const out = applyRetry({ task_type: "service" }, { max_attempts: 50, base_delay_seconds: 2, max_delay_seconds: 9 });
    expect(out).toEqual({
      task_type: "service",
      retry: { max_attempts: MAX_ATTEMPTS_CAP, base_delay_seconds: 2, max_delay_seconds: 9 },
    });
  });

  it("DELETES the retry key when the policy is null (not max_attempts:1)", () => {
    const before = { task_type: "service", retry: { max_attempts: 3 }, config: {} };
    const out = applyRetry(before, null);
    expect(out).not.toHaveProperty("retry");
    expect(out).toEqual({ task_type: "service", config: {} });
    // input is untouched (immutable)
    expect(before).toHaveProperty("retry");
  });
});

describe("applyContinueOnError", () => {
  it("sets the flag when on", () => {
    expect(applyContinueOnError({ task_type: "service" }, true)).toEqual({
      task_type: "service",
      continue_on_error: true,
    });
  });

  it("removes the key when off", () => {
    const before = { task_type: "service", continue_on_error: true };
    const out = applyContinueOnError(before, false);
    expect(out).not.toHaveProperty("continue_on_error");
    expect(before).toHaveProperty("continue_on_error"); // input untouched
  });
});
