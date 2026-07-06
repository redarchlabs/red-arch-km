import type { Edge, Node } from "@xyflow/react";
import { describe, expect, it } from "vitest";

import { checkGraphIntegrity, normalizeForSave, toDefinition, toReactFlow } from "./graphSerde";
import type { WorkflowDefinition } from "@/lib/api/workflows";

describe("graphSerde", () => {
  it("round-trips a definition through React Flow and back", () => {
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
    expect(back.nodes.map((n) => n.id)).toEqual(["t", "c"]);
    expect(back.edges[0].source_handle).toBe("true");
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
    const nodes = [
      { id: "x", type: "bogus", position: { x: 0, y: 0 }, data: {} },
    ] as unknown as Node[];
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

  it("reports a cycle as an error", () => {
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
    expect(result.errors.some((m) => /cycle/i.test(m))).toBe(true);
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
