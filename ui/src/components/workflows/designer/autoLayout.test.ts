import type { Edge, Node } from "@xyflow/react";
import type { ElkNode } from "elkjs/lib/elk.bundled.js";
import { describe, expect, it } from "vitest";

import { applyElkPositions, toElkGraph } from "./autoLayout";

function node(id: string, extra: Partial<Node> = {}): Node {
  return { id, type: "task", position: { x: 0, y: 0 }, data: {}, ...extra };
}

describe("autoLayout", () => {
  describe("toElkGraph", () => {
    it("maps nodes to sized children and edges to source/target lists", () => {
      const nodes = [node("a", { measured: { width: 200, height: 80 } }), node("b")];
      const edges: Edge[] = [{ id: "e1", source: "a", target: "b" }];
      const graph = toElkGraph(nodes, edges);
      expect(graph.children).toEqual([
        { id: "a", width: 200, height: 80 },
        { id: "b", width: 180, height: 72 }, // unmeasured → fallback size
      ]);
      expect(graph.edges).toEqual([{ id: "e1", sources: ["a"], targets: ["b"] }]);
      expect(graph.layoutOptions?.["elk.algorithm"]).toBe("layered");
    });

    it("excludes boundary (parented) nodes and any edge touching them", () => {
      const nodes = [node("host"), node("b1", { parentId: "host" })];
      const edges: Edge[] = [{ id: "e1", source: "host", target: "b1" }];
      const graph = toElkGraph(nodes, edges);
      expect(graph.children?.map((c) => c.id)).toEqual(["host"]);
      expect(graph.edges).toEqual([]);
    });
  });

  describe("applyElkPositions", () => {
    it("moves nodes present in the result and leaves the rest untouched", () => {
      const nodes = [node("a"), node("b", { parentId: "host", position: { x: 5, y: 6 } })];
      const layout: ElkNode = { id: "root", children: [{ id: "a", x: 100, y: 200 }] };
      const out = applyElkPositions(nodes, layout);
      expect(out[0].position).toEqual({ x: 100, y: 200 });
      expect(out[1].position).toEqual({ x: 5, y: 6 }); // boundary child untouched
      expect(out[0]).not.toBe(nodes[0]); // new object (immutable)
    });

    it("ignores result children missing coordinates", () => {
      const nodes = [node("a", { position: { x: 1, y: 2 } })];
      const layout: ElkNode = { id: "root", children: [{ id: "a" }] };
      expect(applyElkPositions(nodes, layout)[0].position).toEqual({ x: 1, y: 2 });
    });
  });
});
