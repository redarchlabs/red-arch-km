/**
 * Connection rules enforced live while dragging an edge. Mirrors the graph
 * semantics the backend/validation care about, but as a *hard* guard (you can't
 * even draw the edge) for the cases that are always wrong:
 *
 *  - no edge may terminate on a start/trigger (starts have no inflow);
 *  - end events have no outgoing edge (a flow terminates there);
 *  - a boundary event is never a target (it only originates an escape path);
 *  - an event-based gateway may only lead to catch events or receive tasks.
 */
import type { Connection, Edge, Node } from "@xyflow/react";

import {
  isBoundaryEvent,
  isEndEvent,
  nodeCategory,
  resolveEventPosition,
  resolveGatewayType,
  resolveTaskType,
} from "@/components/workflows/nodes/nodeMeta";

type NodeLike = Pick<Node, "type" | "data">;

/** A node an event-based gateway is allowed to route to. */
export function isCatchTarget(node: NodeLike): boolean {
  if (nodeCategory(node.type) === "event" && resolveEventPosition(node) === "intermediate") {
    return ((node.data?.throw_catch as string | undefined) ?? "catch") === "catch";
  }
  return node.type === "task" && resolveTaskType(node) === "receive";
}

/**
 * Build a React-Flow `isValidConnection` predicate over the current nodes.
 * Curried so the canvas can memoise it against the node list.
 */
export function isValidConnection(nodes: Node[]): (c: Connection | Edge) => boolean {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  return (c: Connection | Edge): boolean => {
    const source = c.source ? byId.get(c.source) : undefined;
    const target = c.target ? byId.get(c.target) : undefined;
    if (!source || !target) return false;

    // No edge may terminate on a start/trigger.
    if (nodeCategory(target.type) === "trigger") return false;

    // End events have no outgoing edge.
    if (isEndEvent(source)) return false;

    // A boundary event is never a target — it only originates an escape.
    if (isBoundaryEvent(target)) return false;

    // Event-based gateways deferred-choose between catch events / receive tasks.
    if (source.type === "gateway" && resolveGatewayType(source) === "event_based") {
      if (!isCatchTarget(target)) return false;
    }

    return true;
  };
}
