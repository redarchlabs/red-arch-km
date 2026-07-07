import { describe, expect, it } from "vitest";

import { parseSseFrame } from "./runStream";

describe("parseSseFrame", () => {
  it("parses event + data lines", () => {
    expect(parseSseFrame('event: snapshot\ndata: {"run":{"status":"running"}}')).toEqual({
      event: "snapshot",
      data: '{"run":{"status":"running"}}',
    });
  });

  it("defaults the event to message when only data is present", () => {
    expect(parseSseFrame("data: hello")).toEqual({ event: "message", data: "hello" });
  });

  it("joins multiple data lines", () => {
    expect(parseSseFrame("event: x\ndata: a\ndata: b")).toEqual({ event: "x", data: "a\nb" });
  });

  it("returns null for a frame with no data (comments/heartbeats)", () => {
    expect(parseSseFrame(": keep-alive")).toBeNull();
    expect(parseSseFrame("event: done")).toBeNull();
  });
});
