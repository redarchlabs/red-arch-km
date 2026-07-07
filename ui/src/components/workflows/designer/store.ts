/**
 * Designer store — the single source of truth for the canvas graph
 * (nodes/edges/selection/clipboard) plus undo/redo history.
 *
 * Built on zustand + zundo: every mutation to `nodes`/`edges` is recorded so
 * the temporal store gives undo/redo for free (`useDesignerStore.temporal`).
 * Selection lives on the nodes' React Flow `selected` flag; the clipboard holds
 * a detached copy that paste/duplicate remaps to fresh ids.
 */
import {
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  type Connection,
  type Edge,
  type EdgeChange,
  type Node,
  type NodeChange,
  type XYPosition,
} from "@xyflow/react";
import { temporal } from "zundo";
import { create, type StateCreator } from "zustand";
import { createStore } from "zustand/vanilla";

import { newNodeId } from "@/components/workflows/graphSerde";
import { isBoundaryEvent, metaFor } from "@/components/workflows/nodes/nodeMeta";

const RED = "#f43f5e";

function edgeStyle(sourceHandle: string | null | undefined): Edge["style"] {
  return sourceHandle === "false" || sourceHandle === "error" ? { stroke: RED } : undefined;
}

/** Parents must precede their children in a React Flow node array. */
function orderParentsFirst(nodes: Node[]): Node[] {
  return [...nodes.filter((n) => !n.parentId), ...nodes.filter((n) => n.parentId)];
}

/**
 * Clone a slab of nodes (+ the edges wholly inside it) with fresh ids, remapping
 * every cross-reference: edge source/target, boundary `attached_to`, and RF
 * `parentId`. Branch handles (`true`/`false`/`case-*`/`default`) are node-type
 * scoped, not instance scoped, so they carry over unchanged. Result is selected.
 */
function cloneSlab(nodes: Node[], edges: Edge[], offset: XYPosition): { nodes: Node[]; edges: Edge[] } {
  const idMap = new Map<string, string>();
  for (const n of nodes) idMap.set(n.id, newNodeId(n.type ?? "node"));

  const clonedNodes = nodes.map((n) => {
    const data: Record<string, unknown> = { ...(n.data ?? {}) };
    if (typeof data.attached_to === "string" && idMap.has(data.attached_to)) {
      data.attached_to = idMap.get(data.attached_to);
    }
    const cloned: Node = {
      ...n,
      id: idMap.get(n.id) as string,
      selected: true,
      position: { x: n.position.x + offset.x, y: n.position.y + offset.y },
      data,
    };
    if (cloned.parentId && idMap.has(cloned.parentId)) cloned.parentId = idMap.get(cloned.parentId);
    return cloned;
  });

  const clonedEdges = edges.map((e) => ({
    ...e,
    id: newNodeId("e"),
    source: idMap.get(e.source) as string,
    target: idMap.get(e.target) as string,
    selected: false,
  }));

  return { nodes: orderParentsFirst(clonedNodes), edges: clonedEdges };
}

export interface DesignerState {
  nodes: Node[];
  edges: Edge[];
  clipboard: { nodes: Node[]; edges: Edge[] } | null;

  /** Replace the whole graph (load). Caller should clear temporal history after. */
  setGraph: (nodes: Node[], edges: Edge[]) => void;
  onNodesChange: (changes: NodeChange[]) => void;
  onEdgesChange: (changes: EdgeChange[]) => void;
  onConnect: (connection: Connection) => void;
  /** Add a node of `type` at `position`; returns the created node. */
  addNode: (type: string, position: XYPosition, dataOverride?: Record<string, unknown>) => Node;
  updateNodeData: (id: string, data: Record<string, unknown>) => void;
  /** Replace node positions from an auto-layout pass (edges untouched, undoable). */
  applyLayout: (nodes: Node[]) => void;
  /** Delete nodes, cascading their boundary-event children and touching edges. */
  deleteNodes: (ids: string[]) => void;
  deleteEdges: (ids: string[]) => void;
  /** Exclusively select one node (or clear with null). */
  selectNode: (id: string | null) => void;
  copySelection: () => void;
  paste: (offset?: XYPosition) => void;
  duplicateSelection: () => void;
  reset: () => void;
}

const DUP_OFFSET: XYPosition = { x: 40, y: 40 };

const stateCreator: StateCreator<DesignerState> = (set, get) => ({
  nodes: [],
  edges: [],
  clipboard: null,

  setGraph: (nodes, edges) => set({ nodes, edges }),

  onNodesChange: (changes) => set({ nodes: applyNodeChanges(changes, get().nodes) }),

  onEdgesChange: (changes) => set({ edges: applyEdgeChanges(changes, get().edges) }),

  onConnect: (connection) => {
    const edge: Edge = {
      ...connection,
      id: newNodeId("e"),
      type: "labeled",
      style: edgeStyle(connection.sourceHandle),
    };
    set({ edges: addEdge(edge, get().edges) });
  },

  addNode: (type, position, dataOverride) => {
    const node: Node = {
      id: newNodeId(type),
      type,
      position,
      data: dataOverride ?? metaFor(type).defaultData(),
    };
    set({ nodes: [...get().nodes, node] });
    return node;
  },

  updateNodeData: (id, data) =>
    set({ nodes: get().nodes.map((n) => (n.id === id ? { ...n, data } : n)) }),

  applyLayout: (nodes) => set({ nodes: orderParentsFirst(nodes) }),

  deleteNodes: (ids) => {
    const idSet = new Set(ids);
    // Cascade: a boundary event can't outlive the activity it rides.
    for (const n of get().nodes) {
      const host = n.data?.attached_to;
      if (isBoundaryEvent(n) && typeof host === "string" && idSet.has(host)) idSet.add(n.id);
    }
    set({
      nodes: get().nodes.filter((n) => !idSet.has(n.id)),
      edges: get().edges.filter((e) => !idSet.has(e.source) && !idSet.has(e.target)),
    });
  },

  deleteEdges: (ids) => {
    const idSet = new Set(ids);
    set({ edges: get().edges.filter((e) => !idSet.has(e.id)) });
  },

  selectNode: (id) =>
    set({
      nodes: get().nodes.map((n) => ({ ...n, selected: n.id === id })),
      edges: get().edges.map((e) => ({ ...e, selected: false })),
    }),

  copySelection: () => {
    const selNodes = get().nodes.filter((n) => n.selected);
    if (selNodes.length === 0) return;
    const ids = new Set(selNodes.map((n) => n.id));
    const selEdges = get().edges.filter((e) => ids.has(e.source) && ids.has(e.target));
    set({
      clipboard: {
        nodes: selNodes.map((n) => ({ ...n, data: { ...n.data } })),
        edges: selEdges.map((e) => ({ ...e })),
      },
    });
  },

  paste: (offset = DUP_OFFSET) => {
    const clip = get().clipboard;
    if (!clip || clip.nodes.length === 0) return;
    const cloned = cloneSlab(clip.nodes, clip.edges, offset);
    set({
      nodes: [...get().nodes.map((n) => ({ ...n, selected: false })), ...cloned.nodes],
      edges: [...get().edges, ...cloned.edges],
    });
  },

  duplicateSelection: () => {
    const selNodes = get().nodes.filter((n) => n.selected);
    if (selNodes.length === 0) return;
    const ids = new Set(selNodes.map((n) => n.id));
    const selEdges = get().edges.filter((e) => ids.has(e.source) && ids.has(e.target));
    const cloned = cloneSlab(selNodes, selEdges, DUP_OFFSET);
    set({
      nodes: [...get().nodes.map((n) => ({ ...n, selected: false })), ...cloned.nodes],
      edges: [...get().edges, ...cloned.edges],
    });
  },

  reset: () => set({ nodes: [], edges: [], clipboard: null }),
});

/**
 * A structural fingerprint of the graph that ignores volatile React Flow fields
 * (selection, measured dimensions, drag flags). Used both to skip no-op history
 * entries and as the store's "dirty since save" signal.
 */
export function graphSignature(state: { nodes: Node[]; edges: Edge[] }): string {
  const nodes = state.nodes.map((n) => ({
    id: n.id,
    type: n.type ?? null,
    x: Math.round(n.position?.x ?? 0),
    y: Math.round(n.position?.y ?? 0),
    parentId: n.parentId ?? null,
    data: n.data ?? {},
  }));
  const edges = state.edges.map((e) => ({ id: e.id, s: e.source, t: e.target, h: e.sourceHandle ?? null }));
  return JSON.stringify({ nodes, edges });
}

// Only nodes/edges are undoable; clipboard is transient scratch space. Selection
// and auto-measured dimensions must NOT create history (or mark the graph dirty),
// so `equality` skips any change that leaves the structural signature untouched.
const temporalOptions = {
  partialize: (state: DesignerState) => ({ nodes: state.nodes, edges: state.edges }),
  limit: 200,
  equality: (a: { nodes: Node[]; edges: Edge[] }, b: { nodes: Node[]; edges: Edge[] }) =>
    graphSignature(a) === graphSignature(b),
};

/** App singleton (one designer mounts at a time; `load` resets it). */
export const useDesignerStore = create<DesignerState>()(temporal(stateCreator, temporalOptions));

/** Isolated instance factory — used by tests so cases don't share state. */
export const createDesignerStore = () => createStore<DesignerState>()(temporal(stateCreator, temporalOptions));
