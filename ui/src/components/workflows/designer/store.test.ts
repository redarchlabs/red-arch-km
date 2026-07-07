import type { Edge, Node } from "@xyflow/react";
import { beforeEach, describe, expect, it } from "vitest";

import { createDesignerStore, type DesignerState } from "./store";

type Store = ReturnType<typeof createDesignerStore>;

function node(id: string, type: string, data: Record<string, unknown> = {}, extra: Partial<Node> = {}): Node {
  return { id, type, position: { x: 0, y: 0 }, data, ...extra };
}

describe("designer store", () => {
  let store: Store;
  const state = (): DesignerState => store.getState();

  beforeEach(() => {
    store = createDesignerStore();
    state().setGraph([node("trigger", "trigger")], []);
    store.temporal.getState().clear();
  });

  it("addNode appends a node with registry default data and returns it", () => {
    const created = state().addNode("task", { x: 10, y: 20 });
    expect(created.type).toBe("task");
    expect(created.data).toMatchObject({ task_type: "service" });
    expect(state().nodes).toHaveLength(2);
    expect(state().nodes[1].id).toBe(created.id);
  });

  it("updateNodeData replaces a node's data immutably", () => {
    const created = state().addNode("task", { x: 0, y: 0 });
    const before = state().nodes;
    state().updateNodeData(created.id, { task_type: "send" });
    expect(state().nodes.find((n) => n.id === created.id)?.data).toEqual({ task_type: "send" });
    expect(state().nodes).not.toBe(before); // new array
  });

  it("applyLayout replaces node positions and stays undoable", () => {
    const created = state().addNode("task", { x: 0, y: 0 });
    const moved = state().nodes.map((n) =>
      n.id === created.id ? { ...n, position: { x: 300, y: 400 } } : n,
    );
    state().applyLayout(moved);
    expect(state().nodes.find((n) => n.id === created.id)?.position).toEqual({ x: 300, y: 400 });
    store.temporal.getState().undo();
    expect(state().nodes.find((n) => n.id === created.id)?.position).toEqual({ x: 0, y: 0 });
  });

  it("deleteNodes cascades boundary-event children and touching edges", () => {
    state().setGraph(
      [
        node("t1", "task", { task_type: "user" }),
        node("b1", "event", { position: "boundary", event_type: "timer", attached_to: "t1" }),
        node("end", "event", { position: "end", event_type: "none" }),
      ],
      [{ id: "e1", source: "t1", target: "end" }],
    );
    state().deleteNodes(["t1"]);
    expect(state().nodes.map((n) => n.id)).toEqual(["end"]); // b1 cascaded away
    expect(state().edges).toHaveLength(0); // e1 touched t1
  });

  it("selectNode sets the selected flag exclusively", () => {
    const a = state().addNode("task", { x: 0, y: 0 });
    const b = state().addNode("task", { x: 0, y: 0 });
    state().selectNode(b.id);
    expect(state().nodes.find((n) => n.id === b.id)?.selected).toBe(true);
    expect(state().nodes.find((n) => n.id === a.id)?.selected).toBe(false);
  });

  it("undo/redo reverts and replays a node addition", () => {
    expect(state().nodes).toHaveLength(1);
    state().addNode("task", { x: 0, y: 0 });
    expect(state().nodes).toHaveLength(2);
    store.temporal.getState().undo();
    expect(state().nodes).toHaveLength(1);
    store.temporal.getState().redo();
    expect(state().nodes).toHaveLength(2);
  });

  it("copy + paste clones the selection to fresh ids with an offset", () => {
    state().setGraph(
      [
        node("a", "task", { task_type: "service" }, { selected: true, position: { x: 100, y: 100 } }),
        node("b", "event", { position: "end", event_type: "none" }, { selected: true, position: { x: 100, y: 200 } }),
      ],
      [{ id: "e1", source: "a", target: "b", sourceHandle: null } as Edge],
    );
    state().copySelection();
    state().paste({ x: 40, y: 40 });

    expect(state().nodes).toHaveLength(4);
    const originals = new Set(["a", "b"]);
    const pasted = state().nodes.filter((n) => !originals.has(n.id));
    expect(pasted).toHaveLength(2);
    expect(pasted.every((n) => n.selected)).toBe(true);
    // originals are deselected, ids differ, positions offset
    expect(state().nodes.filter((n) => originals.has(n.id)).every((n) => !n.selected)).toBe(true);
    const pastedA = pasted.find((n) => n.type === "task");
    expect(pastedA?.position).toEqual({ x: 140, y: 140 });
    // the internal edge was cloned and rewired to the pasted nodes
    const clonedEdge = state().edges.find((e) => e.id !== "e1");
    expect(clonedEdge).toBeDefined();
    expect(pasted.map((n) => n.id)).toContain(clonedEdge?.source);
    expect(pasted.map((n) => n.id)).toContain(clonedEdge?.target);
  });

  it("paste remaps a boundary child's attached_to to the cloned host", () => {
    state().setGraph(
      [
        node("t1", "task", { task_type: "user" }, { selected: true }),
        node("b1", "event", { position: "boundary", event_type: "timer", attached_to: "t1" }, { selected: true, parentId: "t1", extent: "parent" }),
      ],
      [],
    );
    state().copySelection();
    state().paste();
    const pasted = state().nodes.filter((n) => n.id !== "t1" && n.id !== "b1");
    const host = pasted.find((n) => n.type === "task");
    const boundary = pasted.find((n) => n.data?.position === "boundary");
    expect(boundary?.data?.attached_to).toBe(host?.id);
    expect(boundary?.parentId).toBe(host?.id);
  });

  it("duplicateSelection clones without touching the clipboard", () => {
    state().setGraph([node("a", "task", {}, { selected: true })], []);
    state().duplicateSelection();
    expect(state().nodes).toHaveLength(2);
    expect(state().clipboard).toBeNull();
  });
});
