import { Position } from "@xyflow/react";
import { describe, expect, it } from "vitest";

import {
  handlesFor,
  metaFor,
  nodeCategory,
  resolveEventPosition,
  resolveEventType,
  resolveGatewayType,
  resolveGlyph,
  resolveTaskType,
  subtypeLabel,
} from "./nodeMeta";

describe("nodeCategory", () => {
  it("maps BPMN types to categories", () => {
    expect(nodeCategory("trigger")).toBe("trigger");
    expect(nodeCategory("task")).toBe("activity");
    expect(nodeCategory("gateway")).toBe("gateway");
    expect(nodeCategory("event")).toBe("event");
  });

  it("maps legacy types to their category so old graphs render", () => {
    expect(nodeCategory("action")).toBe("activity");
    expect(nodeCategory("condition")).toBe("gateway");
    expect(nodeCategory("switch")).toBe("gateway");
    expect(nodeCategory("merge")).toBe("gateway");
    expect(nodeCategory("passthrough")).toBe("gateway");
    expect(nodeCategory("delay")).toBe("event");
  });

  it("falls back to activity for unknown types", () => {
    expect(nodeCategory("bogus")).toBe("activity");
    expect(metaFor("bogus").label).toBe("Node");
  });
});

describe("subtype resolution", () => {
  it("resolves task_type with a service default", () => {
    expect(resolveTaskType({ type: "task", data: { task_type: "send" } })).toBe("send");
    expect(resolveTaskType({ type: "task", data: {} })).toBe("service");
    expect(resolveTaskType({ type: "task", data: { task_type: "nope" } })).toBe("service");
  });

  it("resolves gateway_type with an exclusive default", () => {
    expect(resolveGatewayType({ type: "gateway", data: { gateway_type: "parallel" } })).toBe("parallel");
    expect(resolveGatewayType({ type: "gateway", data: {} })).toBe("exclusive");
  });

  it("resolves event position + type", () => {
    expect(resolveEventPosition({ type: "event", data: { position: "end" } })).toBe("end");
    expect(resolveEventPosition({ type: "event", data: {} })).toBe("intermediate");
    expect(resolveEventType({ type: "event", data: { event_type: "timer" } })).toBe("timer");
    expect(resolveEventType({ type: "event", data: {} })).toBe("none");
  });

  it("produces readable word labels for each glyph", () => {
    expect(subtypeLabel({ type: "trigger", data: {} })).toBe("Start");
    expect(subtypeLabel({ type: "task", data: { task_type: "service" } })).toBe("Service task");
    expect(subtypeLabel({ type: "gateway", data: { gateway_type: "parallel" } })).toBe("Parallel gateway");
    expect(subtypeLabel({ type: "event", data: { position: "end", event_type: "terminate" } })).toBe("Terminate end");
    expect(subtypeLabel({ type: "event", data: { position: "boundary", event_type: "timer" } })).toBe("Timer boundary");
    expect(subtypeLabel({ type: "event", data: { position: "intermediate", event_type: "timer" } })).toBe("Timer");
  });
});

describe("resolveGlyph", () => {
  it("selects the marker kind from type + subtype", () => {
    expect(resolveGlyph({ type: "trigger", data: {} })).toEqual({ kind: "trigger" });
    expect(resolveGlyph({ type: "task", data: { task_type: "user" } })).toEqual({ kind: "task", task: "user" });
    expect(resolveGlyph({ type: "gateway", data: { gateway_type: "event_based" } })).toEqual({ kind: "gateway", gateway: "event_based" });
    expect(resolveGlyph({ type: "event", data: { position: "end", event_type: "message" } })).toEqual({ kind: "event", event: "message", position: "end" });
    expect(resolveGlyph({ type: "delay", data: {} })).toEqual({ kind: "event", event: "timer", position: "intermediate" });
  });
});

describe("handlesFor", () => {
  it("trigger has only a source", () => {
    const h = handlesFor({ type: "trigger", data: {} });
    expect(h).toHaveLength(1);
    expect(h[0].type).toBe("source");
  });

  it("task has a top target and bottom source", () => {
    const h = handlesFor({ type: "task", data: { task_type: "service" } });
    expect(h.map((x) => x.type)).toEqual(["target", "source"]);
    expect(h[0].position).toBe(Position.Top);
  });

  it("legacy condition keeps true/false source handle ids", () => {
    const ids = handlesFor({ type: "condition", data: { expr: null } })
      .filter((h) => h.type === "source")
      .map((h) => h.id);
    expect(ids).toEqual(["true", "false"]);
  });

  it("legacy switch keeps case + default handle ids", () => {
    const h = handlesFor({ type: "switch", data: { cases: [{ handle: "case-a", label: "A" }, { handle: "case-b", label: "B" }] } });
    const ids = h.filter((x) => x.type === "source").map((x) => x.id);
    expect(ids).toEqual(["case-a", "case-b", "default"]);
  });

  it("exclusive gateway with an expr branches true/false", () => {
    const ids = handlesFor({ type: "gateway", data: { gateway_type: "exclusive", expr: { var: "x" } } })
      .filter((h) => h.type === "source")
      .map((h) => h.id);
    expect(ids).toEqual(["true", "false"]);
  });

  it("gateway with cases branches case/default", () => {
    const ids = handlesFor({ type: "gateway", data: { gateway_type: "exclusive", cases: [{ handle: "case-x" }] } })
      .filter((h) => h.type === "source")
      .map((h) => h.id);
    expect(ids).toEqual(["case-x", "default"]);
  });

  it("plain parallel gateway has a single default source", () => {
    const sources = handlesFor({ type: "gateway", data: { gateway_type: "parallel" } }).filter((h) => h.type === "source");
    expect(sources).toHaveLength(1);
    expect(sources[0].id).toBeUndefined();
  });

  it("end event has no source (flows terminate there)", () => {
    const h = handlesFor({ type: "event", data: { position: "end", event_type: "none" } });
    expect(h.every((x) => x.type === "target")).toBe(true);
  });

  it("boundary event originates a single escape source", () => {
    const h = handlesFor({ type: "event", data: { position: "boundary", event_type: "timer", attached_to: "t1" } });
    expect(h).toHaveLength(1);
    expect(h[0].type).toBe("source");
    expect(h[0].variant).toBe("boundary");
  });

  it("intermediate event has target + source", () => {
    const h = handlesFor({ type: "event", data: { position: "intermediate", event_type: "timer" } });
    expect(h.map((x) => x.type)).toEqual(["target", "source"]);
  });
});
