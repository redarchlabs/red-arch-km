import { describe, expect, it } from "vitest";

import { coerceSlides, gatesOn } from "./SlideDeck";

describe("coerceSlides", () => {
  it("parses an array of slide objects", () => {
    const out = coerceSlides([{ title: "A", body: "hello" }]);
    expect(out).toHaveLength(1);
    expect(out[0]).toMatchObject({ title: "A", body: "hello", require_video: true });
  });

  it("parses a JSON string payload", () => {
    const out = coerceSlides(JSON.stringify([{ title: "S", body: "b" }]));
    expect(out).toEqual([
      { title: "S", body: "b", image_url: null, video_url: null, require_video: true, notes: null },
    ]);
  });

  it("returns [] for null, non-array, and unparseable strings", () => {
    expect(coerceSlides(null)).toEqual([]);
    expect(coerceSlides(undefined)).toEqual([]);
    expect(coerceSlides(42)).toEqual([]);
    expect(coerceSlides({ not: "an array" })).toEqual([]);
    expect(coerceSlides("{not json")).toEqual([]);
  });

  it("drops non-object and array entries (arrays are typeof 'object')", () => {
    const out = coerceSlides([{ body: "keep" }, ["nested"], null, 7, "str"]);
    expect(out).toHaveLength(1);
    expect(out[0].body).toBe("keep");
  });

  it("coerces malformed field types so they can't crash the renderer", () => {
    // A non-string body/title must not reach marked.parse()/React as a non-string.
    const out = coerceSlides([{ title: 123, body: { rich: true }, image_url: 5, notes: [] }]);
    expect(out[0].title).toBeNull();
    expect(out[0].body).toBe("");
    expect(out[0].image_url).toBeNull();
    expect(out[0].notes).toBeNull();
  });

  it("treats require_video as gate-on unless explicitly false", () => {
    expect(coerceSlides([{ body: "x" }])[0].require_video).toBe(true);
    expect(coerceSlides([{ body: "x", require_video: false }])[0].require_video).toBe(false);
    // A non-boolean is treated as the default (gate on).
    expect(coerceSlides([{ body: "x", require_video: "false" }])[0].require_video).toBe(true);
  });
});

describe("gatesOn", () => {
  it("gates only a slide with a video and require_video not disabled", () => {
    expect(gatesOn({ body: "", video_url: "v.mp4" })).toBe(true);
    expect(gatesOn({ body: "", video_url: "v.mp4", require_video: true })).toBe(true);
    expect(gatesOn({ body: "", video_url: "v.mp4", require_video: false })).toBe(false);
    expect(gatesOn({ body: "", video_url: null })).toBe(false);
    expect(gatesOn({ body: "" })).toBe(false);
    expect(gatesOn(undefined)).toBe(false);
  });
});
