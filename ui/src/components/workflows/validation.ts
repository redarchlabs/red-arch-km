/**
 * Semantic workflow-graph validation — the frontend mirror of the backend
 * authority `services/api/src/api/services/workflow/validation.py`. Codes,
 * severities and reachability semantics match so the designer surfaces the same
 * precise, actionable feedback the API would return on save/publish.
 *
 * Like the backend, checks run against a *normalized* graph (legacy
 * condition/switch/delay/action nodes mapped to their BPMN category) so old and
 * new graphs are validated by one set of rules. Cycles are ALLOWED (the token
 * engine bounds them with a step budget); only a loop with no task/wait step
 * that could spin is warned about.
 */
import type { GraphEdge, GraphNode, WorkflowDefinition } from "@/lib/api/workflows";

import {
  FORKING_GATEWAY_TYPES,
  GATEWAY_TYPES,
  HANDLE_DEFAULT,
  NODE_EVENT,
  NODE_GATEWAY,
  NODE_TASK,
  NODE_TRIGGER,
  SCHEMA_VERSION,
  TASK_TYPES,
  EVENT_POSITIONS,
  EVENT_TYPES,
  WAIT_EVENT_TYPES,
  type GatewayType,
} from "./nodes/nodeMeta";

export type Severity = "error" | "warning";

export interface Issue {
  severity: Severity;
  code: string;
  message: string;
  nodeId?: string;
  edgeId?: string;
}

export function hasErrors(issues: Issue[]): boolean {
  return issues.some((i) => i.severity === "error");
}

// --------------------------------------------------------------------------- //
// Normalization (mirror compat.normalize)
// --------------------------------------------------------------------------- //
interface NormNode {
  id: string;
  type: string;
  data: Record<string, unknown>;
}

const SEND_ACTIONS = new Set(["send_email", "send_webhook", "send_form"]);
const GATEWAY_LEGACY = new Set(["condition", "switch", "merge", "passthrough"]);
const ALL_NODE_TYPES = new Set<string>([
  NODE_TRIGGER,
  NODE_TASK,
  NODE_GATEWAY,
  NODE_EVENT,
  "action",
  "condition",
  "switch",
  "delay",
  "merge",
  "passthrough",
]);
const NODE_ID_RE = /^[A-Za-z0-9_-]{1,64}$/;

function normalizeNode(n: GraphNode): NormNode {
  const data: Record<string, unknown> = { ...(n.data ?? {}) };
  if (n.type === "action") {
    if (data.task_type === undefined)
      data.task_type = SEND_ACTIONS.has(String(data.action_type)) ? "send" : "service";
    return { id: n.id, type: NODE_TASK, data };
  }
  if (GATEWAY_LEGACY.has(n.type)) {
    if (data.gateway_type === undefined) data.gateway_type = "exclusive";
    return { id: n.id, type: NODE_GATEWAY, data };
  }
  if (n.type === "delay") {
    if (data.position === undefined) data.position = "intermediate";
    if (data.event_type === undefined) data.event_type = "timer";
    if (data.throw_catch === undefined) data.throw_catch = "catch";
    return { id: n.id, type: NODE_EVENT, data };
  }
  return { id: n.id, type: n.type, data: data };
}

/** True when a graph should be treated as v2 (mirrors WorkflowDefinitionModel.is_v2). */
function isAuthoredV2(def: WorkflowDefinition): boolean {
  if ((def.schema_version ?? 1) >= SCHEMA_VERSION) return true;
  return (def.nodes ?? []).some(
    (n) => n.type === NODE_TASK || n.type === NODE_GATEWAY || n.type === NODE_EVENT,
  );
}

// --------------------------------------------------------------------------- //
// Structural gate (mirror the Pydantic contract -> "malformed" issues)
// --------------------------------------------------------------------------- //
function structuralIssues(def: WorkflowDefinition): Issue[] {
  const issues: Issue[] = [];
  const seen = new Set<string>();
  for (const n of def.nodes ?? []) {
    if (!NODE_ID_RE.test(n.id)) {
      issues.push({ severity: "error", code: "malformed", message: `node id ${JSON.stringify(n.id)} must match [A-Za-z0-9_-]{1,64}`, nodeId: n.id });
    }
    if (!ALL_NODE_TYPES.has(n.type)) {
      issues.push({ severity: "error", code: "malformed", message: `unknown node type ${JSON.stringify(n.type)}`, nodeId: n.id });
    }
    if (seen.has(n.id)) {
      issues.push({ severity: "error", code: "malformed", message: `duplicate node id ${JSON.stringify(n.id)}`, nodeId: n.id });
    }
    seen.add(n.id);
    subtypeIssue(n, issues);
  }
  for (const e of def.edges ?? []) {
    if (!seen.has(e.source) || !seen.has(e.target)) {
      issues.push({ severity: "error", code: "malformed", message: `Edge ${JSON.stringify(e.id ?? `${e.source}->${e.target}`)} references a node that no longer exists.`, edgeId: e.id ?? undefined });
    }
  }
  return issues;
}

function subtypeIssue(n: GraphNode, issues: Issue[]): void {
  const data = n.data ?? {};
  if (n.type === NODE_TASK && data.task_type != null && !(TASK_TYPES as readonly string[]).includes(String(data.task_type))) {
    issues.push({ severity: "error", code: "malformed", message: `task node ${JSON.stringify(n.id)}: unknown task_type ${JSON.stringify(data.task_type)}`, nodeId: n.id });
  } else if (n.type === NODE_GATEWAY && data.gateway_type != null && !(GATEWAY_TYPES as readonly string[]).includes(String(data.gateway_type))) {
    issues.push({ severity: "error", code: "malformed", message: `gateway node ${JSON.stringify(n.id)}: unknown gateway_type ${JSON.stringify(data.gateway_type)}`, nodeId: n.id });
  } else if (n.type === NODE_EVENT) {
    if (data.position != null && !(EVENT_POSITIONS as readonly string[]).includes(String(data.position))) {
      issues.push({ severity: "error", code: "malformed", message: `event node ${JSON.stringify(n.id)}: unknown position ${JSON.stringify(data.position)}`, nodeId: n.id });
    }
    if (data.event_type != null && !(EVENT_TYPES as readonly string[]).includes(String(data.event_type))) {
      issues.push({ severity: "error", code: "malformed", message: `event node ${JSON.stringify(n.id)}: unknown event_type ${JSON.stringify(data.event_type)}`, nodeId: n.id });
    }
  }
}

// --------------------------------------------------------------------------- //
// Graph helpers
// --------------------------------------------------------------------------- //
type Adjacency = { outEdges: Map<string, GraphEdge[]>; incoming: Map<string, number> };

function adjacency(nodes: NormNode[], edges: GraphEdge[]): Adjacency {
  const outEdges = new Map<string, GraphEdge[]>();
  const incoming = new Map<string, number>();
  for (const n of nodes) outEdges.set(n.id, []);
  for (const e of edges) {
    outEdges.get(e.source)?.push(e);
    incoming.set(e.target, (incoming.get(e.target) ?? 0) + 1);
  }
  return { outEdges, incoming };
}

function isBoundary(n: NormNode): boolean {
  return n.type === NODE_EVENT && n.data.position === "boundary";
}

function boundaryMap(nodes: NormNode[]): Map<string, string[]> {
  const byHost = new Map<string, string[]>();
  for (const n of nodes) {
    if (!isBoundary(n)) continue;
    const host = n.data.attached_to;
    if (typeof host === "string") {
      const list = byHost.get(host) ?? [];
      list.push(n.id);
      byHost.set(host, list);
    }
  }
  return byHost;
}

function isCatchTarget(n: NormNode | undefined): boolean {
  if (!n) return false;
  if (n.type === NODE_EVENT && n.data.position === "intermediate") {
    return (n.data.throw_catch ?? "catch") === "catch";
  }
  return n.type === NODE_TASK && n.data.task_type === "receive";
}

function makesProgress(n: NormNode): boolean {
  if (n.type === NODE_TASK) return true;
  if (n.type === NODE_EVENT) return (WAIT_EVENT_TYPES as readonly string[]).includes(String(n.data.event_type));
  return false;
}

// --------------------------------------------------------------------------- //
// Checks
// --------------------------------------------------------------------------- //
function checkTriggers(nodes: NormNode[], issues: Issue[]): void {
  const triggers = nodes.filter((n) => n.type === NODE_TRIGGER);
  if (triggers.length === 0) {
    issues.push({ severity: "error", code: "no-trigger", message: "The workflow has no trigger/start event, so it can never run." });
  } else if (triggers.length > 1) {
    issues.push({ severity: "warning", code: "multiple-triggers", message: `${triggers.length} start events — a separate run starts from each.` });
  }
}

function checkReachability(nodes: NormNode[], out: Adjacency, issues: Issue[]): void {
  const starts = nodes.filter((n) => n.type === NODE_TRIGGER).map((n) => n.id);
  const byHost = boundaryMap(nodes);
  const seen = new Set<string>();
  const queue = [...starts];
  while (queue.length > 0) {
    const current = queue.shift() as string;
    if (seen.has(current)) continue;
    seen.add(current);
    for (const e of out.outEdges.get(current) ?? []) queue.push(e.target);
    for (const b of byHost.get(current) ?? []) queue.push(b);
  }
  for (const n of nodes) {
    if (n.type === NODE_TRIGGER || seen.has(n.id)) continue;
    issues.push({ severity: "warning", code: "unreachable", message: `Node ${JSON.stringify(n.id)} can't be reached from any start event and won't run.`, nodeId: n.id });
  }
}

function checkGateways(nodes: NormNode[], adj: Adjacency, issues: Issue[]): void {
  for (const n of nodes) {
    if (n.type !== NODE_GATEWAY) continue;
    const gatewayType = ((n.data.gateway_type as GatewayType) || "exclusive") as GatewayType;
    const outCount = (adj.outEdges.get(n.id) ?? []).length;
    const inCount = adj.incoming.get(n.id) ?? 0;
    if ((FORKING_GATEWAY_TYPES as readonly string[]).includes(gatewayType)) {
      if (outCount < 2 && inCount < 2) {
        issues.push({ severity: "warning", code: "degenerate-gateway", message: `${gatewayType} gateway ${JSON.stringify(n.id)} neither forks (needs ≥2 outgoing) nor joins (needs ≥2 incoming).`, nodeId: n.id });
      }
    } else if (gatewayType === "exclusive" && outCount >= 2) {
      const handles = (adj.outEdges.get(n.id) ?? []).map((e) => e.source_handle);
      const branchesOnCondition = n.data.expr != null || Boolean(n.data.cases);
      if (branchesOnCondition && !handles.includes(HANDLE_DEFAULT)) {
        issues.push({ severity: "warning", code: "exclusive-no-default", message: `Exclusive gateway ${JSON.stringify(n.id)} has no default branch; a token matching no condition would stop here.`, nodeId: n.id });
      }
    }
  }
}

function checkBoundaryEvents(nodes: NormNode[], issues: Issue[]): void {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  for (const n of nodes) {
    if (!isBoundary(n)) continue;
    const host = n.data.attached_to;
    if (typeof host !== "string" || host === "") {
      issues.push({ severity: "error", code: "boundary-unattached", message: `Boundary event ${JSON.stringify(n.id)} is not attached to an activity.`, nodeId: n.id });
      continue;
    }
    const hostNode = byId.get(host);
    if (!hostNode) {
      issues.push({ severity: "error", code: "boundary-bad-attach", message: `Boundary event ${JSON.stringify(n.id)} is attached to unknown node ${JSON.stringify(host)}.`, nodeId: n.id });
    } else if (hostNode.type !== NODE_TASK) {
      issues.push({ severity: "warning", code: "boundary-nonactivity", message: `Boundary event ${JSON.stringify(n.id)} should attach to a task, not a ${JSON.stringify(hostNode.type)}.`, nodeId: n.id });
    }
  }
}

function checkEventBasedGateways(nodes: NormNode[], adj: Adjacency, issues: Issue[]): void {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  for (const n of nodes) {
    if (n.type !== NODE_GATEWAY || n.data.gateway_type !== "event_based") continue;
    for (const e of adj.outEdges.get(n.id) ?? []) {
      if (!isCatchTarget(byId.get(e.target))) {
        issues.push({ severity: "error", code: "event-gateway-target", message: `Event-based gateway ${JSON.stringify(n.id)} must lead only to catch events or receive tasks; ${JSON.stringify(e.target)} is not one.`, nodeId: n.id, edgeId: e.id ?? undefined });
      }
    }
  }
}

function checkLoops(nodes: NormNode[], adj: Adjacency, issues: Issue[]): void {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  for (const scc of stronglyConnected(nodes, adj)) {
    if (scc.size === 1) {
      const only = [...scc][0];
      const selfLoop = (adj.outEdges.get(only) ?? []).some((e) => e.target === only);
      if (!selfLoop) continue;
    }
    const progress = [...scc].some((id) => {
      const node = byId.get(id);
      return node ? makesProgress(node) : false;
    });
    if (!progress) {
      const sorted = [...scc].sort();
      issues.push({ severity: "warning", code: "loop-no-progress", message: `Loop through ${JSON.stringify(sorted)} contains no task or wait step and may spin without making progress.`, nodeId: sorted[0] });
    }
  }
}

function checkEndPresence(nodes: NormNode[], issues: Issue[]): void {
  const hasEnd = nodes.some((n) => n.type === NODE_EVENT && n.data.position === "end");
  if (!hasEnd) {
    issues.push({ severity: "warning", code: "no-end-event", message: "No explicit end event; add one so the flow's completion reads clearly." });
  }
}

/** Tarjan's SCC (iterative — safe for any graph size), mirroring the backend. */
function stronglyConnected(nodes: NormNode[], adj: Adjacency): Set<string>[] {
  const indexOf = new Map<string, number>();
  const low = new Map<string, number>();
  const onStack = new Set<string>();
  const stack: string[] = [];
  const sccs: Set<string>[] = [];
  let counter = 0;

  for (const root of nodes.map((n) => n.id)) {
    if (indexOf.has(root)) continue;
    const work: [string, number][] = [[root, 0]];
    while (work.length > 0) {
      const [node, childIdx] = work[work.length - 1];
      if (childIdx === 0) {
        indexOf.set(node, counter);
        low.set(node, counter);
        counter += 1;
        stack.push(node);
        onStack.add(node);
      }
      const targets = adj.outEdges.get(node) ?? [];
      if (childIdx < targets.length) {
        work[work.length - 1] = [node, childIdx + 1];
        const next = targets[childIdx].target;
        if (!indexOf.has(next)) {
          work.push([next, 0]);
        } else if (onStack.has(next)) {
          low.set(node, Math.min(low.get(node) ?? 0, indexOf.get(next) ?? 0));
        }
      } else {
        if ((low.get(node) ?? 0) === (indexOf.get(node) ?? 0)) {
          const component = new Set<string>();
          for (;;) {
            const w = stack.pop() as string;
            onStack.delete(w);
            component.add(w);
            if (w === node) break;
          }
          sccs.push(component);
        }
        work.pop();
        if (work.length > 0) {
          const parent = work[work.length - 1][0];
          low.set(parent, Math.min(low.get(parent) ?? 0, low.get(node) ?? 0));
        }
      }
    }
  }
  return sccs;
}

// --------------------------------------------------------------------------- //
// Public entry point
// --------------------------------------------------------------------------- //
/** Return every semantic issue for a workflow graph (empty = clean). */
export function validateGraph(def: WorkflowDefinition | null | undefined): Issue[] {
  if (!def) return [{ severity: "error", code: "malformed", message: "definition is not a valid workflow graph" }];

  const structural = structuralIssues(def);
  if (structural.length > 0) return structural;

  const authoredV2 = isAuthoredV2(def);
  const nodes = (def.nodes ?? []).map(normalizeNode);
  const adj = adjacency(nodes, def.edges ?? []);

  const issues: Issue[] = [];
  checkTriggers(nodes, issues);
  checkReachability(nodes, adj, issues);
  checkGateways(nodes, adj, issues);
  checkBoundaryEvents(nodes, issues);
  checkEventBasedGateways(nodes, adj, issues);
  checkLoops(nodes, adj, issues);
  if (authoredV2) checkEndPresence(nodes, issues);
  return issues;
}
