import type { Edge, Node } from "@xyflow/react";

import { ACTION_CONFIG_FIELDS } from "@/components/workflows/actionTypes";
import type { NodeType, WorkflowDefinition } from "@/lib/api/workflows";

import { SCHEMA_VERSION } from "./nodes/nodeMeta";
import { validateGraph } from "./validation";

/**
 * The node types the backend understands (BPMN categories + still-supported
 * legacy types); anything else is a serialisation bug. Kept in sync with
 * `NodeType` in `lib/api/workflows.ts` and the backend `constants.NODE_TYPES`.
 */
export const NODE_TYPES = [
  "trigger",
  "task",
  "gateway",
  "event",
  "condition",
  "action",
  "switch",
  "delay",
  "merge",
  "passthrough",
] as const;

function isNodeType(type: unknown): type is NodeType {
  return typeof type === "string" && (NODE_TYPES as readonly string[]).includes(type);
}

const ORIGIN = { x: 0, y: 0 };

/** Convert a stored definition into React Flow nodes/edges. */
export function toReactFlow(definition: WorkflowDefinition | undefined): {
  nodes: Node[];
  edges: Edge[];
} {
  const raw = definition?.nodes ?? [];
  const absPosById = new Map(raw.map((n) => [n.id, n.position ?? ORIGIN]));

  const nodes: Node[] = raw.map((n) => {
    const position = n.position ?? ORIGIN;
    const base: Node = { id: n.id, type: n.type, position, data: { ...n.data } };
    // A boundary event rides its host activity: give React Flow a `parentId`
    // (+ `extent: 'parent'`) so it moves with and clips to the host. The stored
    // position is absolute; convert to parent-relative for RF. Both are stripped
    // on `toDefinition`, so the persisted schema stays clean (attached_to only).
    if (n.type === "event" && n.data?.position === "boundary") {
      const host = n.data?.attached_to;
      if (typeof host === "string" && absPosById.has(host)) {
        const parentPos = absPosById.get(host) ?? ORIGIN;
        return {
          ...base,
          parentId: host,
          extent: "parent",
          position: { x: position.x - parentPos.x, y: position.y - parentPos.y },
        };
      }
    }
    return base;
  });

  // React Flow requires a parent node to precede its children in the array.
  const ordered = [...nodes.filter((n) => !n.parentId), ...nodes.filter((n) => n.parentId)];

  const edges: Edge[] = (definition?.edges ?? []).map((e) => ({
    id: e.id,
    source: e.source,
    target: e.target,
    sourceHandle: e.source_handle ?? undefined,
    type: "labeled",
    // Colour "false"/"error" branch edges so the graph reads at a glance.
    style: e.source_handle === "false" || e.source_handle === "error" ? { stroke: "#f43f5e" } : undefined,
  }));
  return { nodes: ordered, edges };
}

/** Convert React Flow nodes/edges back into a stored definition (schema_version 2). */
export function toDefinition(nodes: Node[], edges: Edge[]): WorkflowDefinition {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  return {
    schema_version: SCHEMA_VERSION,
    nodes: nodes.map((n) => {
      // Never silently default an unknown type to "action" — that would persist
      // a mislabelled node the backend then mishandles. Fail loudly instead.
      if (!isNodeType(n.type)) {
        throw new Error(`Cannot save node "${n.id}": unknown node type "${String(n.type)}".`);
      }
      // Programmatically-added nodes may lack a position; default to the origin.
      let position = n.position ?? ORIGIN;
      // A parented (boundary) node's position is parent-relative in React Flow;
      // restore the absolute canvas position and drop the RF-only parent/extent.
      if (n.parentId) {
        const parent = byId.get(n.parentId);
        if (parent) {
          const pp = parent.position ?? ORIGIN;
          position = { x: position.x + pp.x, y: position.y + pp.y };
        }
      }
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
 * Back-compat wrapper over {@link validateGraph}. Cycles are now ALLOWED (the
 * token engine bounds loops with a step budget); only `error`-severity issues
 * (missing trigger, dangling edges, unattached boundary) block a save/publish.
 * Prefer {@link validateGraph} directly for structured, node-keyed issues.
 */
export function checkGraphIntegrity(def: WorkflowDefinition): GraphIntegrity {
  const issues = validateGraph(def);
  return {
    errors: issues.filter((i) => i.severity === "error").map((i) => i.message),
    warnings: issues.filter((i) => i.severity === "warning").map((i) => i.message),
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
      // Legacy `action` nodes and BPMN `task` nodes both carry action config.
      if (node.type !== "action" && node.type !== "task") return node;
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
