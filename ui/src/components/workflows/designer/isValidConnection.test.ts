import type { Connection, Node } from "@xyflow/react";
import { describe, expect, it } from "vitest";

import { isCatchTarget, isValidConnection } from "./isValidConnection";

function node(id: string, type: string, data: Record<string, unknown> = {}): Node {
  return { id, type, position: { x: 0, y: 0 }, data };
}

const conn = (source: string, target: string): Connection => ({ source, target, sourceHandle: null, targetHandle: null });

describe("isValidConnection", () => {
  const nodes = [
    node("start", "trigger"),
    node("t1", "task", { task_type: "service" }),
    node("recv", "task", { task_type: "receive" }),
    node("end", "event", { position: "end", event_type: "none" }),
    node("timer", "event", { position: "intermediate", event_type: "timer", throw_catch: "catch" }),
    node("gw", "gateway", { gateway_type: "event_based" }),
    node("exgw", "gateway", { gateway_type: "exclusive" }),
    node("b1", "event", { position: "boundary", event_type: "error", attached_to: "t1" }),
  ];
  const check = isValidConnection(nodes);

  it("rejects an edge into a start/trigger", () => {
    expect(check(conn("t1", "start"))).toBe(false);
  });

  it("rejects an edge out of an end event", () => {
    expect(check(conn("end", "t1"))).toBe(false);
  });

  it("rejects an edge into a boundary event", () => {
    expect(check(conn("t1", "b1"))).toBe(false);
  });

  it("allows a plain task -> task edge", () => {
    expect(check(conn("t1", "recv"))).toBe(true);
  });

  it("allows a normal node to reach an end event", () => {
    expect(check(conn("t1", "end"))).toBe(true);
  });

  it("event-based gateway may only reach catch events / receive tasks", () => {
    expect(check(conn("gw", "t1"))).toBe(false); // plain service task
    expect(check(conn("gw", "timer"))).toBe(true); // intermediate catch
    expect(check(conn("gw", "recv"))).toBe(true); // receive task
  });

  it("a non-event gateway is unrestricted in its targets", () => {
    expect(check(conn("exgw", "t1"))).toBe(true);
  });

  it("rejects a connection referencing an unknown node", () => {
    expect(check(conn("ghost", "t1"))).toBe(false);
  });

  it("isCatchTarget recognises intermediate catch events and receive tasks", () => {
    expect(isCatchTarget(node("x", "event", { position: "intermediate", event_type: "timer" }))).toBe(true);
    expect(isCatchTarget(node("x", "task", { task_type: "receive" }))).toBe(true);
    expect(isCatchTarget(node("x", "task", { task_type: "service" }))).toBe(false);
  });
});
