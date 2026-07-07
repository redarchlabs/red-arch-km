import { describe, expect, it } from "vitest";

import { isErrorBoundaryEvent, isErrorEdge } from "./errorEdge";

const boundaryError = { type: "event", data: { position: "boundary", event_type: "error" } };
const boundaryTimer = { type: "event", data: { position: "boundary", event_type: "timer" } };
const task = { type: "task", data: { task_type: "service" } };

describe("isErrorBoundaryEvent", () => {
  it("is true only for a boundary event with event_type error", () => {
    expect(isErrorBoundaryEvent(boundaryError)).toBe(true);
  });

  it("is false for a non-error boundary event", () => {
    expect(isErrorBoundaryEvent(boundaryTimer)).toBe(false);
  });

  it("is false for an intermediate error event", () => {
    expect(isErrorBoundaryEvent({ type: "event", data: { position: "intermediate", event_type: "error" } })).toBe(false);
  });

  it("is false for a non-event node and for undefined", () => {
    expect(isErrorBoundaryEvent(task)).toBe(false);
    expect(isErrorBoundaryEvent(undefined)).toBe(false);
  });
});

describe("isErrorEdge", () => {
  it("is true for the reserved error and boundary source handles", () => {
    expect(isErrorEdge(task, "error")).toBe(true);
    expect(isErrorEdge(task, "boundary")).toBe(true);
  });

  it("is true for an edge leaving an error boundary event even with no handle id", () => {
    expect(isErrorEdge(boundaryError, null)).toBe(true);
    expect(isErrorEdge(boundaryError, undefined)).toBe(true);
  });

  it("is false for the plain false branch (red, but not an error edge)", () => {
    expect(isErrorEdge({ type: "condition", data: { expr: null } }, "false")).toBe(false);
  });

  it("is false for a happy-path edge", () => {
    expect(isErrorEdge(task, "true")).toBe(false);
    expect(isErrorEdge(task, null)).toBe(false);
    expect(isErrorEdge(undefined, null)).toBe(false);
  });

  it("honours the handle even when the source node is unknown", () => {
    expect(isErrorEdge(undefined, "error")).toBe(true);
  });
});
