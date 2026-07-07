import type { Edge, Node } from "@xyflow/react";
import { describe, expect, it } from "vitest";

import { checkGraphIntegrity, normalizeForSave, toDefinition, toReactFlow } from "./graphSerde";
import type { WorkflowDefinition } from "@/lib/api/workflows";

describe("graphSerde", () => {
  it("round-trips a definition through React Flow and back at schema_version 2", () => {
    const def: WorkflowDefinition = {
      schema_version: 1,
      nodes: [
        { id: "t", type: "trigger", position: { x: 1, y: 2 }, data: { operations: ["update"] } },
        { id: "c", type: "condition", position: { x: 3, y: 4 }, data: { expr: null } },
      ],
      edges: [{ id: "e1", source: "t", target: "c", source_handle: "true" }],
    };
    const rf = toReactFlow(def);
    expect(rf.edges[0].sourceHandle).toBe("true");
    const back = toDefinition(rf.nodes, rf.edges);
    expect(back.schema_version).toBe(2);
    expect(back.nodes.map((n) => n.id)).toEqual(["t", "c"]);
    expect(back.edges[0].source_handle).toBe("true");
  });

  it("round-trips a boundary event's attached_to via parentId/extent (no schema pollution)", () => {
    const def: WorkflowDefinition = {
      schema_version: 2,
      nodes: [
        // Deliberately list the child before its host to prove ordering.
        { id: "b1", type: "event", position: { x: 140, y: 150 }, data: { position: "boundary", event_type: "timer", attached_to: "t1" } },
        { id: "t1", type: "task", position: { x: 100, y: 100 }, data: { task_type: "user" } },
      ],
      edges: [],
    };
    const rf = toReactFlow(def);
    // React Flow requires the parent to precede the child.
    expect(rf.nodes.map((n) => n.id)).toEqual(["t1", "b1"]);
    const boundary = rf.nodes.find((n) => n.id === "b1") as Node;
    expect(boundary.parentId).toBe("t1");
    expect(boundary.extent).toBe("parent");
    // Absolute (140,150) becomes parent-relative (40,50).
    expect(boundary.position).toEqual({ x: 40, y: 50 });

    const back = toDefinition(rf.nodes, rf.edges);
    const b = back.nodes.find((n) => n.id === "b1");
    const asRecord = b as unknown as Record<string, unknown>;
    expect(b?.data.attached_to).toBe("t1");
    expect(asRecord.parentId).toBeUndefined();
    expect(asRecord.extent).toBeUndefined();
    // Absolute position is restored.
    expect(b?.position).toEqual({ x: 140, y: 150 });
  });

  it("parses JSON action config fields on save", () => {
    const nodes: Node[] = [
      {
        id: "a",
        type: "action",
        position: { x: 0, y: 0 },
        data: { action_type: "create_record", config: { target_slug: "task", values: '{"title":"x"}' } },
      },
    ];
    const def = normalizeForSave(toDefinition(nodes, [] as Edge[]));
    expect(def.nodes[0].data.config).toEqual({ target_slug: "task", values: { title: "x" } });
  });

  it("parses JSON config on a BPMN task node too", () => {
    const nodes: Node[] = [
      {
        id: "a",
        type: "task",
        position: { x: 0, y: 0 },
        data: { task_type: "service", action_type: "create_record", config: { target_slug: "task", values: '{"title":"x"}' } },
      },
    ];
    const def = normalizeForSave(toDefinition(nodes, [] as Edge[]));
    expect((def.nodes[0].data.config as Record<string, unknown>).values).toEqual({ title: "x" });
  });

  it("leaves invalid JSON config as a string for backend validation", () => {
    const nodes: Node[] = [
      {
        id: "a",
        type: "action",
        position: { x: 0, y: 0 },
        data: { action_type: "create_record", config: { values: "{not json" } },
      },
    ];
    const def = normalizeForSave(toDefinition(nodes, [] as Edge[]));
    expect((def.nodes[0].data.config as Record<string, unknown>).values).toBe("{not json");
  });

  it("throws on an unknown node type instead of defaulting to 'action'", () => {
    const nodes = [{ id: "x", type: "bogus", position: { x: 0, y: 0 }, data: {} }] as unknown as Node[];
    expect(() => toDefinition(nodes, [] as Edge[])).toThrow(/unknown node type/i);
  });

  it("defaults a missing node position to the origin", () => {
    const nodes = [{ id: "t", type: "trigger", data: {} }] as unknown as Node[];
    const def = toDefinition(nodes, [] as Edge[]);
    expect(def.nodes[0].position).toEqual({ x: 0, y: 0 });
  });
});

describe("checkGraphIntegrity", () => {
  function def(partial: Partial<WorkflowDefinition>): WorkflowDefinition {
    return { schema_version: 1, nodes: [], edges: [], ...partial };
  }

  it("passes a clean connected graph", () => {
    const result = checkGraphIntegrity(
      def({
        nodes: [
          { id: "t", type: "trigger", position: { x: 0, y: 0 }, data: {} },
          { id: "a", type: "action", position: { x: 0, y: 0 }, data: {} },
        ],
        edges: [{ id: "e1", source: "t", target: "a" }],
      }),
    );
    expect(result.errors).toEqual([]);
    expect(result.warnings).toEqual([]);
  });

  it("reports a dangling edge as an error", () => {
    const result = checkGraphIntegrity(
      def({
        nodes: [{ id: "t", type: "trigger", position: { x: 0, y: 0 }, data: {} }],
        edges: [{ id: "e1", source: "t", target: "ghost" }],
      }),
    );
    expect(result.errors.some((m) => m.includes("e1"))).toBe(true);
  });

  it("ALLOWS a cycle (the token engine bounds loops with a step budget)", () => {
    const result = checkGraphIntegrity(
      def({
        nodes: [
          { id: "t", type: "trigger", position: { x: 0, y: 0 }, data: {} },
          { id: "a", type: "action", position: { x: 0, y: 0 }, data: {} },
        ],
        edges: [
          { id: "e1", source: "t", target: "a" },
          { id: "e2", source: "a", target: "t" },
        ],
      }),
    );
    expect(result.errors).toEqual([]);
  });

  it("warns about an unreachable non-trigger node without blocking", () => {
    const result = checkGraphIntegrity(
      def({
        nodes: [
          { id: "t", type: "trigger", position: { x: 0, y: 0 }, data: {} },
          { id: "orphan", type: "action", position: { x: 0, y: 0 }, data: {} },
        ],
        edges: [],
      }),
    );
    expect(result.errors).toEqual([]);
    expect(result.warnings.some((m) => m.includes("orphan"))).toBe(true);
  });
});
