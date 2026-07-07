import type { NodeChrome, NodeRunStatus } from "@/components/workflows/nodes/BaseNode";

/**
 * Live-run overlay mapping: turn a backend run snapshot's per-node status into the
 * {@link NodeChrome} the canvas status-ring consumes. Pure + unit-tested; the SSE
 * hook and the polling fallback both feed through here so the colours are
 * consistent regardless of transport.
 */

/** Backend node/step/token status → the 5-state ring vocabulary. */
export function mapRunStatus(status: string | undefined): NodeRunStatus {
  switch (status) {
    case "succeeded":
    case "skipped":
      return "completed";
    case "failed":
      return "failed";
    case "running":
      return "active";
    case "waiting":
    case "retrying":
      return "waiting";
    default:
      return "idle";
  }
}

/** A snapshot's ``nodes`` map ({nodeId: backendStatus}) → chrome for the canvas. */
export function chromeFromNodeStatuses(nodes: Record<string, string>): Record<string, NodeChrome> {
  const chrome: Record<string, NodeChrome> = {};
  for (const [nodeId, status] of Object.entries(nodes ?? {})) {
    chrome[nodeId] = { status: mapRunStatus(status) };
  }
  return chrome;
}

/** The live snapshot pushed over SSE (mirrors the backend `_run_stream_snapshot`). */
export interface RunSnapshot {
  run: { id: string; status: string; dead_letter: boolean; error: string | null };
  nodes: Record<string, string>;
  tokens: { node_id: string; status: string; wait_kind: string | null }[];
}

/** Build the node-status map from run steps + live tokens (polling fallback path),
 * matching the server snapshot's precedence: a recorded step status wins; a node
 * holding only a live token shows running/waiting. */
export function nodeStatusesFromSteps(
  steps: { node_id: string; status: string }[],
  tokens: { node_id: string; status: string }[],
): Record<string, string> {
  const nodes: Record<string, string> = {};
  for (const step of steps) nodes[step.node_id] = step.status;
  for (const token of tokens) {
    if (token.status === "active" || token.status === "running") {
      nodes[token.node_id] ??= "running";
    } else if (token.status === "waiting") {
      nodes[token.node_id] ??= "waiting";
    }
  }
  return nodes;
}
