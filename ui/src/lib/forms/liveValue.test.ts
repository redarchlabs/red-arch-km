import { describe, expect, it } from "vitest";

import { displayLiveValue, formatLiveValue, readJsonPointer } from "./liveValue";

describe("readJsonPointer", () => {
  const body = { head: { pitch: -12.5 }, thinking: true, names: ["a", "b"] };

  it("returns the whole body for a blank pointer", () => {
    expect(readJsonPointer(body, null)).toBe(body);
    expect(readJsonPointer(body, "")).toBe(body);
  });

  it("walks a dot path", () => {
    expect(readJsonPointer(body, "head.pitch")).toBe(-12.5);
    expect(readJsonPointer(body, "thinking")).toBe(true);
  });

  it("yields undefined when the path runs off the data", () => {
    expect(readJsonPointer(body, "head.roll")).toBeUndefined();
    expect(readJsonPointer(body, "head.pitch.deeper")).toBeUndefined();
    expect(readJsonPointer(null, "head")).toBeUndefined();
  });
});

describe("formatLiveValue", () => {
  it("shows an em dash for nothing", () => {
    expect(formatLiveValue(null)).toBe("—");
    expect(formatLiveValue(undefined)).toBe("—");
  });

  it("stringifies scalars, including false and zero", () => {
    expect(formatLiveValue(-12.5)).toBe("-12.5");
    expect(formatLiveValue(false)).toBe("false");
    expect(formatLiveValue(0)).toBe("0");
  });

  it("serialises objects and arrays as JSON", () => {
    expect(formatLiveValue({ pitch: 1 })).toBe('{"pitch":1}');
    expect(formatLiveValue(["a"])).toBe('["a"]');
  });
});

describe("displayLiveValue", () => {
  const flag = { true: "Thinking…", false: "idle" };

  it("passes the value through with no map", () => {
    expect(displayLiveValue("true")).toBe("true");
    expect(displayLiveValue("true", null)).toBe("true");
  });

  it("translates a mapped value", () => {
    expect(displayLiveValue("true", flag)).toBe("Thinking…");
    expect(displayLiveValue("false", flag)).toBe("idle");
  });

  it("leaves values the map does not name alone", () => {
    // A partial map must relabel only what it lists — the unreachable placeholder
    // has to keep saying the readout is broken.
    expect(displayLiveValue("unreachable", flag)).toBe("unreachable");
    expect(displayLiveValue("—", { true: "on" })).toBe("—");
  });
});
