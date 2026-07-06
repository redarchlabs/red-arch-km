import type { Edge, Node } from "@xyflow/react";
import { describe, expect, it } from "vitest";

import { normalizeForSave, toDefinition, toReactFlow } from "./graphSerde";
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
});
