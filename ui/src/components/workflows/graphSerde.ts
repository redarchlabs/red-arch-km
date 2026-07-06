import type { Edge, Node } from "@xyflow/react";

import { ACTION_CONFIG_FIELDS } from "@/components/workflows/actionTypes";
import type { WorkflowDefinition } from "@/lib/api/workflows";

/** Convert a stored definition into React Flow nodes/edges. */
export function toReactFlow(definition: WorkflowDefinition | undefined): {
  nodes: Node[];
  edges: Edge[];
} {
  const nodes: Node[] = (definition?.nodes ?? []).map((n) => ({
    id: n.id,
    type: n.type,
    position: n.position ?? { x: 0, y: 0 },
    data: { ...n.data },
  }));
  const edges: Edge[] = (definition?.edges ?? []).map((e) => ({
    id: e.id,
    source: e.source,
    target: e.target,
    sourceHandle: e.source_handle ?? undefined,
    // Colour "false" branch edges so the graph reads at a glance.
    style: e.source_handle === "false" ? { stroke: "#f43f5e" } : undefined,
  }));
  return { nodes, edges };
}

/** Convert React Flow nodes/edges back into a stored definition. */
export function toDefinition(nodes: Node[], edges: Edge[]): WorkflowDefinition {
  return {
    schema_version: 1,
    nodes: nodes.map((n) => ({
      id: n.id,
      type: (n.type ?? "action") as WorkflowDefinition["nodes"][number]["type"],
      position: { x: Math.round(n.position.x), y: Math.round(n.position.y) },
      data: (n.data ?? {}) as Record<string, unknown>,
    })),
    edges: edges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      source_handle: (e.sourceHandle ?? null) as string | null,
    })),
  };
}

/** A fresh single-trigger starter graph for a brand-new workflow. */
export function starterGraph(): { nodes: Node[]; edges: Edge[] } {
  return {
    nodes: [
      {
        id: "trigger",
        type: "trigger",
        position: { x: 240, y: 40 },
        data: { operations: ["update"], field_filter: [] },
      },
    ],
    edges: [],
  };
}

export function newNodeId(prefix: string): string {
  return `${prefix}_${Math.random().toString(36).slice(2, 9)}`;
}

/**
 * Parse action-node JSON config fields (edited as text) into real objects
 * before persisting. Invalid JSON is left as-is so the backend surfaces a
 * clear validation error rather than the UI silently dropping it.
 */
export function normalizeForSave(definition: WorkflowDefinition): WorkflowDefinition {
  return {
    ...definition,
    nodes: definition.nodes.map((node) => {
      if (node.type !== "action") return node;
      const actionType = String(node.data?.action_type ?? "");
      const jsonKeys = (ACTION_CONFIG_FIELDS[actionType] ?? [])
        .filter((f) => f.type === "json")
        .map((f) => f.key);
      if (jsonKeys.length === 0) return node;
      const config = { ...((node.data?.config as Record<string, unknown>) ?? {}) };
      for (const key of jsonKeys) {
        const raw = config[key];
        if (typeof raw === "string" && raw.trim() !== "") {
          try {
            config[key] = JSON.parse(raw);
          } catch {
            // leave as string; backend validation reports it
          }
        }
      }
      return { ...node, data: { ...node.data, config } };
    }),
  };
}
