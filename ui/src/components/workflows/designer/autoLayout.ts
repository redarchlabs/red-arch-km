/**
 * ELK-powered auto-layout for the designer canvas. `elkjs` is dynamically
 * imported inside {@link layoutGraph} so the (fairly large) layout engine never
 * enters the initial designer bundle — it loads only when the user clicks
 * "Auto-layout". The graph<->ELK transforms are pure and unit-tested; only the
 * `elk.layout` call itself needs a browser, so it stays isolated.
 */
import type { Edge, Node } from "@xyflow/react";
import type { ElkNode } from "elkjs/lib/elk.bundled.js";

/** Fallbacks when a node hasn't been measured yet (roughly a task card). */
const DEFAULT_NODE_WIDTH = 180;
const DEFAULT_NODE_HEIGHT = 72;

/** ELK options tuned for a readable top-down BPMN-style flow. */
export const LAYOUT_OPTIONS: Record<string, string> = {
  "elk.algorithm": "layered",
  "elk.direction": "DOWN",
  "elk.layered.spacing.nodeNodeBetweenLayers": "64",
  "elk.spacing.nodeNode": "48",
  "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
};

function nodeWidth(n: Node): number {
  return n.measured?.width ?? n.width ?? DEFAULT_NODE_WIDTH;
}

function nodeHeight(n: Node): number {
  return n.measured?.height ?? n.height ?? DEFAULT_NODE_HEIGHT;
}

/**
 * Build an ELK graph from the canvas nodes/edges. Boundary (parented) nodes are
 * excluded from layout — they ride their host activity and keep their relative
 * offset — and any edge touching an excluded node is dropped from the ELK graph.
 */
export function toElkGraph(nodes: Node[], edges: Edge[]): ElkNode {
  const laidOut = nodes.filter((n) => !n.parentId);
  const ids = new Set(laidOut.map((n) => n.id));
  return {
    id: "root",
    layoutOptions: LAYOUT_OPTIONS,
    children: laidOut.map((n) => ({ id: n.id, width: nodeWidth(n), height: nodeHeight(n) })),
    edges: edges
      .filter((e) => ids.has(e.source) && ids.has(e.target))
      .map((e) => ({ id: e.id, sources: [e.source], targets: [e.target] })),
  };
}

/**
 * Return NEW nodes with positions from an ELK result. Nodes absent from the
 * result (e.g. boundary children) keep their existing position. Never mutates.
 */
export function applyElkPositions(nodes: Node[], layout: ElkNode): Node[] {
  const positions = new Map<string, { x: number; y: number }>();
  for (const child of layout.children ?? []) {
    if (typeof child.x === "number" && typeof child.y === "number") {
      positions.set(child.id, { x: child.x, y: child.y });
    }
  }
  return nodes.map((n) => {
    const p = positions.get(n.id);
    return p ? { ...n, position: p } : n;
  });
}

/**
 * Run ELK 'layered' layout over the graph and resolve to nodes with updated
 * positions. `elkjs` is imported lazily; the caller pushes the result through
 * the store's `applyLayout`.
 */
export async function layoutGraph(nodes: Node[], edges: Edge[]): Promise<Node[]> {
  const { default: ELK } = await import("elkjs/lib/elk.bundled.js");
  const elk = new ELK();
  const result = await elk.layout(toElkGraph(nodes, edges));
  return applyElkPositions(nodes, result);
}
