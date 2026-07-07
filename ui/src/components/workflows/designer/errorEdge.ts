/**
 * Error-path detection for designer edges. An edge follows a node's *error*
 * path — the unhappy branch a boundary error event or a task's error handle
 * routes to — and is rendered visually distinct (dashed orange) from the normal
 * happy-path and the plain false branch.
 *
 * Pure predicate (no React, no store) so `LabeledEdge` and its test share one
 * source of truth for what counts as an error edge.
 */
import { HANDLE_BOUNDARY, HANDLE_ERROR } from "@/components/workflows/nodes/nodeMeta";

type NodeLike = { type?: string; data?: Record<string, unknown> | null } | undefined;

/** Distinct stroke for the unhappy path (orange-500), set apart from the red false branch. */
export const ERROR_EDGE_COLOR = "#f97316";

/**
 * A boundary event whose `event_type` is `error` — the BPMN catch that fires
 * when the activity it rides throws. Its out-edges are error edges even though
 * the sole source handle carries no id.
 */
export function isErrorBoundaryEvent(node: NodeLike): boolean {
  if (node?.type !== "event") return false;
  const data = node.data ?? {};
  return data.position === "boundary" && data.event_type === "error";
}

/**
 * An edge is an error edge when it leaves the reserved `error`/`boundary` source
 * handle, or when its source node is an error boundary event.
 */
export function isErrorEdge(sourceNode: NodeLike, sourceHandle: string | null | undefined): boolean {
  if (sourceHandle === HANDLE_ERROR || sourceHandle === HANDLE_BOUNDARY) return true;
  return isErrorBoundaryEvent(sourceNode);
}
