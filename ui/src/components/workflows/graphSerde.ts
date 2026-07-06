import type { Edge, Node } from "@xyflow/react";

import { ACTION_CONFIG_FIELDS } from "@/components/workflows/actionTypes";
import type { NodeType, WorkflowDefinition } from "@/lib/api/workflows";

/** The node types the backend understands; anything else is a serialisation bug. */
export const NODE_TYPES = ["trigger", "condition", "action", "switch", "delay"] as const;

function isNodeType(type: unknown): type is NodeType {
  return typeof type === "string" && (NODE_TYPES as readonly string[]).includes(type);
}

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
    nodes: nodes.map((n) => {
      // Never silently default an unknown type to "action" — that would persist
      // a mislabelled node the backend then mishandles. Fail loudly instead.
      if (!isNodeType(n.type)) {
        throw new Error(`Cannot save node "${n.id}": unknown node type "${String(n.type)}".`);
      }
      // Programmatically-added nodes may lack a position; default to the origin.
      const position = n.position ?? { x: 0, y: 0 };
      return {
        id: n.id,
        type: n.type,
        position: { x: Math.round(position.x), y: Math.round(position.y) },
        data: (n.data ?? {}) as Record<string, unknown>,
      };
    }),
    edges: edges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      source_handle: (e.sourceHandle ?? null) as string | null,
    })),
  };
}

export interface GraphIntegrity {
  /** Structural problems that must block a save/publish. */
  errors: string[];
  /** Non-fatal issues worth surfacing (e.g. an unreachable node). */
  warnings: string[];
}

/**
 * Structural integrity checks run before save/publish: dangling edges and
 * cycles are hard errors (the run engine walks the graph forward and would
 * loop or dereference a missing node); orphaned non-trigger nodes are warnings.
 */
export function checkGraphIntegrity(def: WorkflowDefinition): GraphIntegrity {
  const errors: string[] = [];
  const warnings: string[] = [];
  const ids = new Set(def.nodes.map((n) => n.id));

  for (const e of def.edges) {
    if (!ids.has(e.source) || !ids.has(e.target)) {
      errors.push(`Edge "${e.id}" connects a node that no longer exists.`);
    }
  }

  if (hasCycle(def)) {
    errors.push("The graph contains a cycle; a workflow must be acyclic.");
  }

  const withIncoming = new Set(def.edges.map((e) => e.target));
  for (const n of def.nodes) {
    if (n.type !== "trigger" && !withIncoming.has(n.id)) {
      warnings.push(`Node "${n.id}" is not connected to anything and won't run.`);
    }
  }

  return { errors, warnings };
}

function hasCycle(def: WorkflowDefinition): boolean {
  const adjacency = new Map<string, string[]>();
  for (const n of def.nodes) adjacency.set(n.id, []);
  for (const e of def.edges) adjacency.get(e.source)?.push(e.target);

  const WHITE = 0;
  const GRAY = 1;
  const BLACK = 2;
  const color = new Map<string, number>();
  for (const id of adjacency.keys()) color.set(id, WHITE);

  const visit = (id: string): boolean => {
    color.set(id, GRAY);
    for (const next of adjacency.get(id) ?? []) {
      const c = color.get(next);
      if (c === GRAY) return true; // back-edge → cycle
      if (c === WHITE && visit(next)) return true;
    }
    color.set(id, BLACK);
    return false;
  };

  for (const id of adjacency.keys()) {
    if (color.get(id) === WHITE && visit(id)) return true;
  }
  return false;
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
