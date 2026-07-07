import { describe, expect, it } from "vitest";

import type { WorkflowDefinition } from "@/lib/api/workflows";

import { hasErrors, validateGraph, type Issue } from "./validation";

/** Mirrors the backend suite (tests/unit/test_workflow_validation.py) rule-for-rule. */
function codes(defn: WorkflowDefinition): Set<string> {
  return new Set(validateGraph(defn).map((i) => i.code));
}

describe("validateGraph", () => {
  it("clean linear graph has no issues", () => {
    const defn: WorkflowDefinition = {
      schema_version: 2,
      nodes: [
        { id: "start", type: "trigger", position: { x: 0, y: 0 }, data: {} },
        { id: "t1", type: "task", position: { x: 0, y: 0 }, data: { task_type: "service" } },
        { id: "end1", type: "event", position: { x: 0, y: 0 }, data: { position: "end", event_type: "none" } },
      ],
      edges: [
        { id: "e1", source: "start", target: "t1" },
        { id: "e2", source: "t1", target: "end1" },
      ],
    };
    expect(validateGraph(defn)).toEqual([]);
  });

  it("missing trigger is an error", () => {
    const defn: WorkflowDefinition = {
      schema_version: 2,
      nodes: [{ id: "t1", type: "task", position: { x: 0, y: 0 }, data: { task_type: "service" } }],
      edges: [],
    };
    const issues = validateGraph(defn);
    expect(issues.some((i) => i.code === "no-trigger" && i.severity === "error")).toBe(true);
    expect(hasErrors(issues)).toBe(true);
  });

  it("multiple triggers warns", () => {
    const defn: WorkflowDefinition = {
      schema_version: 2,
      nodes: [
        { id: "s1", type: "trigger", position: { x: 0, y: 0 }, data: {} },
        { id: "s2", type: "trigger", position: { x: 0, y: 0 }, data: {} },
      ],
      edges: [],
    };
    expect(codes(defn).has("multiple-triggers")).toBe(true);
  });

  it("unreachable node warns with its id", () => {
    const defn: WorkflowDefinition = {
      schema_version: 2,
      nodes: [
        { id: "start", type: "trigger", position: { x: 0, y: 0 }, data: {} },
        { id: "island", type: "task", position: { x: 0, y: 0 }, data: { task_type: "service" } },
      ],
      edges: [],
    };
    const issues = validateGraph(defn);
    expect(issues.some((i) => i.code === "unreachable" && i.nodeId === "island")).toBe(true);
  });

  it("boundary reachable via host is not unreachable", () => {
    const defn: WorkflowDefinition = {
      schema_version: 2,
      nodes: [
        { id: "start", type: "trigger", position: { x: 0, y: 0 }, data: {} },
        { id: "a1", type: "task", position: { x: 0, y: 0 }, data: { task_type: "service" } },
        { id: "b1", type: "event", position: { x: 0, y: 0 }, data: { position: "boundary", event_type: "timer", attached_to: "a1" } },
      ],
      edges: [{ id: "e1", source: "start", target: "a1" }],
    };
    expect(codes(defn).has("unreachable")).toBe(false);
  });

  it("boundary unattached is an error", () => {
    const defn: WorkflowDefinition = {
      schema_version: 2,
      nodes: [
        { id: "start", type: "trigger", position: { x: 0, y: 0 }, data: {} },
        { id: "b1", type: "event", position: { x: 0, y: 0 }, data: { position: "boundary", event_type: "error" } },
      ],
      edges: [],
    };
    expect(codes(defn).has("boundary-unattached")).toBe(true);
  });

  it("boundary attached to unknown node is an error", () => {
    const defn: WorkflowDefinition = {
      schema_version: 2,
      nodes: [
        { id: "start", type: "trigger", position: { x: 0, y: 0 }, data: {} },
        { id: "b1", type: "event", position: { x: 0, y: 0 }, data: { position: "boundary", event_type: "error", attached_to: "ghost" } },
      ],
      edges: [],
    };
    expect(codes(defn).has("boundary-bad-attach")).toBe(true);
  });

  it("boundary on a non-task warns", () => {
    const defn: WorkflowDefinition = {
      schema_version: 2,
      nodes: [
        { id: "start", type: "trigger", position: { x: 0, y: 0 }, data: {} },
        { id: "gw", type: "gateway", position: { x: 0, y: 0 }, data: { gateway_type: "exclusive" } },
        { id: "b1", type: "event", position: { x: 0, y: 0 }, data: { position: "boundary", event_type: "timer", attached_to: "gw" } },
      ],
      edges: [{ id: "e1", source: "start", target: "gw" }],
    };
    expect(codes(defn).has("boundary-nonactivity")).toBe(true);
  });

  it("exclusive gateway without default warns", () => {
    const defn: WorkflowDefinition = {
      schema_version: 2,
      nodes: [
        { id: "start", type: "trigger", position: { x: 0, y: 0 }, data: {} },
        { id: "gw", type: "gateway", position: { x: 0, y: 0 }, data: { gateway_type: "exclusive", expr: { var: "after.x" } } },
        { id: "a", type: "task", position: { x: 0, y: 0 }, data: { task_type: "service" } },
        { id: "b", type: "task", position: { x: 0, y: 0 }, data: { task_type: "service" } },
      ],
      edges: [
        { id: "e1", source: "start", target: "gw" },
        { id: "e2", source: "gw", target: "a", source_handle: "true" },
        { id: "e3", source: "gw", target: "b", source_handle: "false" },
      ],
    };
    expect(codes(defn).has("exclusive-no-default")).toBe(true);
  });

  it("exclusive gateway with default is ok", () => {
    const defn: WorkflowDefinition = {
      schema_version: 2,
      nodes: [
        { id: "start", type: "trigger", position: { x: 0, y: 0 }, data: {} },
        { id: "gw", type: "gateway", position: { x: 0, y: 0 }, data: { gateway_type: "exclusive", expr: { var: "after.x" } } },
        { id: "a", type: "task", position: { x: 0, y: 0 }, data: { task_type: "service" } },
        { id: "b", type: "task", position: { x: 0, y: 0 }, data: { task_type: "service" } },
      ],
      edges: [
        { id: "e1", source: "start", target: "gw" },
        { id: "e2", source: "gw", target: "a", source_handle: "true" },
        { id: "e3", source: "gw", target: "b", source_handle: "default" },
      ],
    };
    expect(codes(defn).has("exclusive-no-default")).toBe(false);
  });

  it("parallel gateway with a single branch warns (degenerate)", () => {
    const defn: WorkflowDefinition = {
      schema_version: 2,
      nodes: [
        { id: "start", type: "trigger", position: { x: 0, y: 0 }, data: {} },
        { id: "gw", type: "gateway", position: { x: 0, y: 0 }, data: { gateway_type: "parallel" } },
        { id: "a", type: "task", position: { x: 0, y: 0 }, data: { task_type: "service" } },
      ],
      edges: [
        { id: "e1", source: "start", target: "gw" },
        { id: "e2", source: "gw", target: "a" },
      ],
    };
    expect(codes(defn).has("degenerate-gateway")).toBe(true);
  });

  it("parallel fork/join is clean", () => {
    const defn: WorkflowDefinition = {
      schema_version: 2,
      nodes: [
        { id: "start", type: "trigger", position: { x: 0, y: 0 }, data: {} },
        { id: "fork", type: "gateway", position: { x: 0, y: 0 }, data: { gateway_type: "parallel" } },
        { id: "a", type: "task", position: { x: 0, y: 0 }, data: { task_type: "service" } },
        { id: "b", type: "task", position: { x: 0, y: 0 }, data: { task_type: "service" } },
        { id: "join", type: "gateway", position: { x: 0, y: 0 }, data: { gateway_type: "parallel" } },
        { id: "end", type: "event", position: { x: 0, y: 0 }, data: { position: "end", event_type: "none" } },
      ],
      edges: [
        { id: "e1", source: "start", target: "fork" },
        { id: "e2", source: "fork", target: "a" },
        { id: "e3", source: "fork", target: "b" },
        { id: "e4", source: "a", target: "join" },
        { id: "e5", source: "b", target: "join" },
        { id: "e6", source: "join", target: "end" },
      ],
    };
    expect(validateGraph(defn)).toEqual([]);
  });

  it("event-based gateway to a plain task is an error", () => {
    const defn: WorkflowDefinition = {
      schema_version: 2,
      nodes: [
        { id: "start", type: "trigger", position: { x: 0, y: 0 }, data: {} },
        { id: "gw", type: "gateway", position: { x: 0, y: 0 }, data: { gateway_type: "event_based" } },
        { id: "t", type: "task", position: { x: 0, y: 0 }, data: { task_type: "service" } },
      ],
      edges: [
        { id: "e1", source: "start", target: "gw" },
        { id: "e2", source: "gw", target: "t" },
      ],
    };
    const issues = validateGraph(defn);
    expect(issues.some((i) => i.code === "event-gateway-target" && i.severity === "error")).toBe(true);
  });

  it("event-based gateway to catch events / receive is ok", () => {
    const defn: WorkflowDefinition = {
      schema_version: 2,
      nodes: [
        { id: "start", type: "trigger", position: { x: 0, y: 0 }, data: {} },
        { id: "gw", type: "gateway", position: { x: 0, y: 0 }, data: { gateway_type: "event_based" } },
        { id: "timer", type: "event", position: { x: 0, y: 0 }, data: { position: "intermediate", event_type: "timer", throw_catch: "catch" } },
        { id: "recv", type: "task", position: { x: 0, y: 0 }, data: { task_type: "receive" } },
      ],
      edges: [
        { id: "e1", source: "start", target: "gw" },
        { id: "e2", source: "gw", target: "timer" },
        { id: "e3", source: "gw", target: "recv" },
      ],
    };
    expect(codes(defn).has("event-gateway-target")).toBe(false);
  });

  it("progressless loop warns", () => {
    const defn: WorkflowDefinition = {
      schema_version: 2,
      nodes: [
        { id: "start", type: "trigger", position: { x: 0, y: 0 }, data: {} },
        { id: "g1", type: "gateway", position: { x: 0, y: 0 }, data: { gateway_type: "exclusive" } },
        { id: "g2", type: "gateway", position: { x: 0, y: 0 }, data: { gateway_type: "exclusive" } },
      ],
      edges: [
        { id: "e1", source: "start", target: "g1" },
        { id: "e2", source: "g1", target: "g2" },
        { id: "e3", source: "g2", target: "g1" },
      ],
    };
    expect(codes(defn).has("loop-no-progress")).toBe(true);
  });

  it("loop with a task is allowed (cycles are permitted)", () => {
    const defn: WorkflowDefinition = {
      schema_version: 2,
      nodes: [
        { id: "start", type: "trigger", position: { x: 0, y: 0 }, data: {} },
        { id: "gw", type: "gateway", position: { x: 0, y: 0 }, data: { gateway_type: "exclusive", expr: { var: "vars.done" } } },
        { id: "work", type: "task", position: { x: 0, y: 0 }, data: { task_type: "service" } },
      ],
      edges: [
        { id: "e1", source: "start", target: "gw" },
        { id: "e2", source: "gw", target: "work", source_handle: "default" },
        { id: "e3", source: "work", target: "gw" },
      ],
    };
    expect(codes(defn).has("loop-no-progress")).toBe(false);
  });

  it("v2 graph without an end event warns", () => {
    const defn: WorkflowDefinition = {
      schema_version: 2,
      nodes: [
        { id: "start", type: "trigger", position: { x: 0, y: 0 }, data: {} },
        { id: "t", type: "task", position: { x: 0, y: 0 }, data: { task_type: "service" } },
      ],
      edges: [{ id: "e1", source: "start", target: "t" }],
    };
    expect(codes(defn).has("no-end-event")).toBe(true);
  });

  it("legacy (v1) graph is not penalized for a missing end event", () => {
    const defn: WorkflowDefinition = {
      schema_version: 1,
      nodes: [
        { id: "start", type: "trigger", position: { x: 0, y: 0 }, data: { operations: ["update"] } },
        { id: "a", type: "action", position: { x: 0, y: 0 }, data: { action_type: "log" } },
      ],
      edges: [{ id: "e1", source: "start", target: "a" }],
    };
    expect(codes(defn).has("no-end-event")).toBe(false);
  });

  it("legacy action loop makes progress (no warning)", () => {
    const defn: WorkflowDefinition = {
      schema_version: 1,
      nodes: [
        { id: "start", type: "trigger", position: { x: 0, y: 0 }, data: {} },
        { id: "c", type: "condition", position: { x: 0, y: 0 }, data: { expr: null } },
        { id: "a", type: "action", position: { x: 0, y: 0 }, data: { action_type: "log" } },
      ],
      edges: [
        { id: "e1", source: "start", target: "c" },
        { id: "e2", source: "c", target: "a", source_handle: "true" },
        { id: "e3", source: "a", target: "c" },
      ],
    };
    expect(codes(defn).has("loop-no-progress")).toBe(false);
  });

  it("malformed definition returns a malformed error issue", () => {
    const issues: Issue[] = validateGraph({ nodes: [{ id: "bad id", type: "task", position: { x: 0, y: 0 }, data: {} }], edges: [] });
    expect(hasErrors(issues)).toBe(true);
    expect(issues.some((i) => i.code === "malformed")).toBe(true);
  });

  it("dangling edge is reported as an error", () => {
    const issues = validateGraph({
      schema_version: 2,
      nodes: [{ id: "start", type: "trigger", position: { x: 0, y: 0 }, data: {} }],
      edges: [{ id: "e1", source: "start", target: "ghost" }],
    });
    expect(issues.some((i) => i.code === "malformed" && i.message.includes("e1"))).toBe(true);
  });

  it("null definition is malformed, not a crash", () => {
    expect(hasErrors(validateGraph(null))).toBe(true);
  });
});
