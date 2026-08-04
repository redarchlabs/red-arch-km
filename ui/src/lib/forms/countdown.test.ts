import { describe, expect, it } from "vitest";

import { countdownState, formatRemaining, parseInstant } from "./countdown";

const T0 = Date.parse("2026-08-03T19:00:00Z");

describe("parseInstant", () => {
  it("reads an offset-bearing timestamp", () => {
    expect(parseInstant("2026-08-03T19:00:00+00:00")).toBe(T0);
    expect(parseInstant("2026-08-03T19:00:00Z")).toBe(T0);
    expect(parseInstant("2026-08-03T13:00:00-06:00")).toBe(T0);
  });

  it("reads a timestamp with no zone as UTC, not as the viewer's local time", () => {
    // The bug this guards: a naive string read as local time turns a 20-second
    // question into a countdown hours long (or one that is already over).
    expect(parseInstant("2026-08-03T19:00:00")).toBe(T0);
    expect(parseInstant("2026-08-03 19:00:00")).toBe(T0);
  });

  it("returns null for anything that isn't a time", () => {
    expect(parseInstant(null)).toBeNull();
    expect(parseInstant("")).toBeNull();
    expect(parseInstant("soon")).toBeNull();
    expect(parseInstant({})).toBeNull();
  });
});

describe("countdownState", () => {
  const el = { from_field: "question_opened_at", seconds: 20 };
  const values = { question_opened_at: "2026-08-03T19:00:00Z" };

  it("counts down from a start plus a duration", () => {
    expect(countdownState(el, values, T0).remainingMs).toBe(20_000);
    expect(countdownState(el, values, T0 + 5_000).remainingMs).toBe(15_000);
    expect(countdownState(el, values, T0 + 20_000).remainingMs).toBe(0);
  });

  it("floors at zero rather than counting up into negatives", () => {
    expect(countdownState(el, values, T0 + 90_000).remainingMs).toBe(0);
  });

  it("caps at the full span, so a device with a wrong clock isn't given extra time", () => {
    expect(countdownState(el, values, T0 - 3_600_000).remainingMs).toBe(20_000);
  });

  it("takes the duration from a field when one is named", () => {
    const state = countdownState(
      { from_field: "question_opened_at", seconds: 20, seconds_field: "seconds_allowed" },
      { ...values, seconds_allowed: 45 },
      T0,
    );
    expect(state).toEqual({ remainingMs: 45_000, totalMs: 45_000 });
  });

  it("prefers an absolute deadline over start-plus-duration", () => {
    const state = countdownState(
      { until_field: "closes_at", from_field: "question_opened_at", seconds: 20 },
      { ...values, closes_at: "2026-08-03T19:00:10Z" },
      T0,
    );
    expect(state.remainingMs).toBe(10_000);
  });

  it("reports nothing when the record carries no deadline", () => {
    // What lets a countdown sit on a page between questions instead of being
    // gated: no deadline, no clock, rather than a stuck 0:00.
    expect(countdownState(el, {}, T0)).toEqual({ remainingMs: null, totalMs: null });
    expect(countdownState({ seconds: 20 }, values, T0).remainingMs).toBeNull();
    expect(countdownState({ from_field: "question_opened_at" }, values, T0).remainingMs).toBeNull();
  });

  it("has no span to draw a bar against when only a deadline is known", () => {
    const state = countdownState({ until_field: "closes_at" }, { closes_at: "2026-08-03T19:00:10Z" }, T0);
    expect(state).toEqual({ remainingMs: 10_000, totalMs: null });
  });
});

describe("formatRemaining", () => {
  it("counts bare seconds under a minute", () => {
    expect(formatRemaining(20_000)).toBe("20");
    expect(formatRemaining(1)).toBe("1");
    expect(formatRemaining(0)).toBe("0");
  });

  it("rounds up, so the clock only reads 0 when time is actually gone", () => {
    expect(formatRemaining(19_400)).toBe("20");
  });

  it("switches to m:ss at a minute", () => {
    expect(formatRemaining(60_000)).toBe("1:00");
    expect(formatRemaining(64_000)).toBe("1:04");
  });
});
