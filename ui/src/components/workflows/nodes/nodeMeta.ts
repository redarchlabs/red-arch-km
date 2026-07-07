/**
 * Node vocabulary registry — the single source of truth the palette, serde
 * defaults, canvas glyphs, validation and (later) the command palette key off.
 *
 * Mirrors the backend authority `services/api/src/api/services/workflow/
 * constants.py` (schema_version 2). A node's `type` is a BPMN *category* that
 * drives the on-canvas shape; the concrete subtype lives in `data`
 * (`task_type` / `gateway_type` / event `position` + `event_type`). Legacy
 * types (action/condition/switch/delay/merge/passthrough) are retained and
 * mapped to their category so old graphs still render and validate.
 */
import { Position } from "@xyflow/react";
import {
  Bolt,
  Circle,
  Clock,
  Cog,
  Diamond,
  FileCode,
  GitBranch,
  GitMerge,
  Hand,
  Inbox,
  Mail,
  MoveRight,
  PhoneCall,
  Split,
  SquarePlus,
  Table,
  User,
  Zap,
  type LucideIcon,
} from "lucide-react";

// --------------------------------------------------------------------------- //
// Vocabulary constants (mirror constants.py)
// --------------------------------------------------------------------------- //
export const SCHEMA_VERSION = 2;

export const NODE_TRIGGER = "trigger";
export const NODE_TASK = "task";
export const NODE_GATEWAY = "gateway";
export const NODE_EVENT = "event";

export const NEW_NODE_TYPES = [NODE_TRIGGER, NODE_TASK, NODE_GATEWAY, NODE_EVENT] as const;
export const LEGACY_NODE_TYPES = ["action", "condition", "switch", "delay", "merge", "passthrough"] as const;

// task_type (on a `task` node)
export const TASK_TYPES = [
  "service",
  "send",
  "script",
  "businessRule",
  "user",
  "receive",
  "call",
  "subProcess",
  "manual",
] as const;
export type TaskType = (typeof TASK_TYPES)[number];
/** Wait-state task types park a token until an external signal. */
export const WAIT_TASK_TYPES: readonly TaskType[] = ["user", "receive", "call", "subProcess", "manual"];

// gateway_type (on a `gateway` node)
export const GATEWAY_TYPES = ["exclusive", "parallel", "inclusive", "event_based"] as const;
export type GatewayType = (typeof GATEWAY_TYPES)[number];
export const FORKING_GATEWAY_TYPES: readonly GatewayType[] = ["parallel", "inclusive"];

// event position + type (`data.position` / `data.event_type`)
export const EVENT_POSITIONS = ["intermediate", "end", "boundary"] as const;
export type EventPosition = (typeof EVENT_POSITIONS)[number];
export const EVENT_TYPES = [
  "timer",
  "message",
  "signal",
  "error",
  "escalation",
  "terminate",
  "none",
] as const;
export type EventType = (typeof EVENT_TYPES)[number];
/** Event types a boundary/intermediate CATCH parks a token on until fired. */
export const WAIT_EVENT_TYPES: readonly EventType[] = ["timer", "message", "signal"];

// Reserved edge `source_handle` values
export const HANDLE_TRUE = "true";
export const HANDLE_FALSE = "false";
export const HANDLE_DEFAULT = "default";
export const HANDLE_ERROR = "error";
export const HANDLE_BOUNDARY = "boundary";
export const RESERVED_HANDLES = [
  HANDLE_TRUE,
  HANDLE_FALSE,
  HANDLE_DEFAULT,
  HANDLE_ERROR,
  HANDLE_BOUNDARY,
] as const;

// --------------------------------------------------------------------------- //
// Categories, shapes, accents
// --------------------------------------------------------------------------- //
export type Category = "trigger" | "activity" | "gateway" | "event";
export type Shape = "rect" | "circle" | "diamond";

export type AccentName = "emerald" | "sky" | "amber" | "violet" | "slate" | "indigo" | "rose";

export interface Accent {
  /** border colour for the unselected node shell */
  border: string;
  /** header glyph / accent text colour */
  text: string;
  /** handle background (`!bg-*` wins over React Flow's default) */
  handle: string;
  /** soft chip background for sub-labels */
  soft: string;
  /** raw stroke colour for inline SVG / edges */
  stroke: string;
}

/**
 * Concrete Tailwind class strings per accent. These MUST be literal (Tailwind
 * cannot see `bg-${x}-500`), so every accent lists its classes in full.
 */
export const ACCENTS: Record<AccentName, Accent> = {
  emerald: { border: "border-emerald-500/60", text: "text-emerald-500", handle: "!bg-emerald-500", soft: "bg-emerald-500/10", stroke: "#10b981" },
  sky: { border: "border-sky-500/60", text: "text-sky-500", handle: "!bg-sky-500", soft: "bg-sky-500/10", stroke: "#0ea5e9" },
  amber: { border: "border-amber-500/60", text: "text-amber-500", handle: "!bg-amber-500", soft: "bg-amber-500/10", stroke: "#f59e0b" },
  violet: { border: "border-violet-500/60", text: "text-violet-500", handle: "!bg-violet-500", soft: "bg-violet-500/10", stroke: "#8b5cf6" },
  slate: { border: "border-slate-400/70", text: "text-slate-500", handle: "!bg-slate-400", soft: "bg-slate-500/10", stroke: "#64748b" },
  indigo: { border: "border-indigo-500/60", text: "text-indigo-500", handle: "!bg-indigo-500", soft: "bg-indigo-500/10", stroke: "#6366f1" },
  rose: { border: "border-rose-500/60", text: "text-rose-500", handle: "!bg-rose-500", soft: "bg-rose-500/10", stroke: "#f43f5e" },
};

// --------------------------------------------------------------------------- //
// Handle spec
// --------------------------------------------------------------------------- //
export type HandleVariant = "neutral" | "true" | "false" | "default" | "error" | "boundary";

export interface HandleSpec {
  /** React Flow handle id (edge `source_handle`); omit for the sole/default handle. */
  id?: string;
  type: "source" | "target";
  position: Position;
  /** 0..1 fraction across the node edge when several handles share a side. */
  offset?: number;
  /** short word rendered next to a branch handle. */
  label?: string;
  variant?: HandleVariant;
}

export const HANDLE_VARIANT_ACCENT: Record<HandleVariant, AccentName> = {
  neutral: "slate",
  true: "emerald",
  false: "rose",
  default: "slate",
  error: "rose",
  boundary: "amber",
};

// --------------------------------------------------------------------------- //
// Registry
// --------------------------------------------------------------------------- //
export interface NodeMeta {
  category: Category;
  label: string;
  /** lucide icon for the palette / task-corner (BPMN markers use glyphs.tsx). */
  icon: LucideIcon;
  accent: AccentName;
  shape: Shape;
  /** starter `data` for a freshly-dropped node of this type. */
  defaultData: () => Record<string, unknown>;
  /** legacy types are hidden from the palette but still render/validate. */
  legacy?: boolean;
}

export const NODE_META: Record<string, NodeMeta> = {
  trigger: {
    category: "trigger",
    label: "Trigger",
    icon: Zap,
    accent: "emerald",
    shape: "rect",
    defaultData: () => ({ operations: ["update"], field_filter: [] }),
  },
  task: {
    category: "activity",
    label: "Task",
    icon: Bolt,
    accent: "sky",
    shape: "rect",
    defaultData: () => ({ task_type: "service", action_type: "", config: {} }),
  },
  gateway: {
    category: "gateway",
    label: "Gateway",
    icon: Diamond,
    accent: "amber",
    shape: "diamond",
    defaultData: () => ({ gateway_type: "exclusive" }),
  },
  event: {
    category: "event",
    label: "Event",
    icon: Circle,
    accent: "indigo",
    shape: "circle",
    defaultData: () => ({ position: "end", event_type: "none" }),
  },
  // ---- legacy (rendered + validated, hidden from palette) ---------------- //
  action: {
    category: "activity",
    label: "Action",
    icon: Bolt,
    accent: "sky",
    shape: "rect",
    legacy: true,
    defaultData: () => ({ action_type: "", config: {} }),
  },
  condition: {
    category: "gateway",
    label: "Condition",
    icon: GitBranch,
    accent: "amber",
    shape: "rect",
    legacy: true,
    defaultData: () => ({ expr: null }),
  },
  switch: {
    category: "gateway",
    label: "Switch",
    icon: Split,
    accent: "violet",
    shape: "rect",
    legacy: true,
    defaultData: () => ({ cases: [] }),
  },
  delay: {
    category: "event",
    label: "Delay",
    icon: Clock,
    accent: "slate",
    shape: "rect",
    legacy: true,
    defaultData: () => ({ delay_amount: 30, delay_unit: "minutes", delay_seconds: 1800 }),
  },
  merge: {
    category: "gateway",
    label: "Merge",
    icon: GitMerge,
    accent: "amber",
    shape: "rect",
    legacy: true,
    defaultData: () => ({}),
  },
  passthrough: {
    category: "gateway",
    label: "Passthrough",
    icon: MoveRight,
    accent: "slate",
    shape: "rect",
    legacy: true,
    defaultData: () => ({}),
  },
};

const FALLBACK_META: NodeMeta = {
  category: "activity",
  label: "Node",
  icon: Bolt,
  accent: "slate",
  shape: "rect",
  defaultData: () => ({}),
};

export function metaFor(type: string | undefined): NodeMeta {
  return (type && NODE_META[type]) || FALLBACK_META;
}

export function nodeCategory(type: string | undefined): Category {
  return metaFor(type).category;
}

/** Node types offered in the palette (new vocabulary only, in BPMN order). */
export const PALETTE_TYPES = ["trigger", "task", "gateway", "event"] as const;

// --------------------------------------------------------------------------- //
// Subtype resolution (type + data -> glyph / word label)
// --------------------------------------------------------------------------- //
type NodeLike = { type?: string; data?: Record<string, unknown> | null };

const asData = (node: NodeLike): Record<string, unknown> => node.data ?? {};

export function resolveTaskType(node: NodeLike): TaskType {
  const t = asData(node).task_type;
  return (TASK_TYPES as readonly string[]).includes(t as string) ? (t as TaskType) : "service";
}

export function resolveGatewayType(node: NodeLike): GatewayType {
  const g = asData(node).gateway_type;
  return (GATEWAY_TYPES as readonly string[]).includes(g as string) ? (g as GatewayType) : "exclusive";
}

export function resolveEventPosition(node: NodeLike): EventPosition {
  const p = asData(node).position;
  return (EVENT_POSITIONS as readonly string[]).includes(p as string) ? (p as EventPosition) : "intermediate";
}

export function resolveEventType(node: NodeLike): EventType {
  const e = asData(node).event_type;
  return (EVENT_TYPES as readonly string[]).includes(e as string) ? (e as EventType) : "none";
}

export const TASK_LABELS: Record<TaskType, string> = {
  service: "Service",
  send: "Send",
  script: "Script",
  businessRule: "Business rule",
  user: "User",
  receive: "Receive",
  call: "Call",
  subProcess: "Sub-process",
  manual: "Manual",
};

export const TASK_ICONS: Record<TaskType, LucideIcon> = {
  service: Cog,
  send: Mail,
  script: FileCode,
  businessRule: Table,
  user: User,
  receive: Inbox,
  call: PhoneCall,
  subProcess: SquarePlus,
  manual: Hand,
};

export const GATEWAY_LABELS: Record<GatewayType, string> = {
  exclusive: "Exclusive",
  parallel: "Parallel",
  inclusive: "Inclusive",
  event_based: "Event-based",
};

export const EVENT_TYPE_LABELS: Record<EventType, string> = {
  timer: "Timer",
  message: "Message",
  signal: "Signal",
  error: "Error",
  escalation: "Escalation",
  terminate: "Terminate",
  none: "Plain",
};

const EVENT_POSITION_SUFFIX: Record<EventPosition, string> = {
  intermediate: "",
  end: "end",
  boundary: "boundary",
};

/**
 * A short, readable word label for a node's concrete subtype — every canvas
 * glyph gets one (e.g. "Service", "Exclusive", "Timer boundary").
 */
export function subtypeLabel(node: NodeLike): string {
  const category = nodeCategory(node.type);
  if (category === "trigger") return "Start";
  if (category === "activity") {
    if (node.type === "action") return metaFor(node.type).label;
    return `${TASK_LABELS[resolveTaskType(node)]} task`;
  }
  if (category === "gateway") {
    if (node.type === "condition") return "Condition";
    if (node.type === "switch") return "Switch";
    return `${GATEWAY_LABELS[resolveGatewayType(node)]} gateway`;
  }
  // event
  if (node.type === "delay") return "Delay";
  const type = EVENT_TYPE_LABELS[resolveEventType(node)];
  const suffix = EVENT_POSITION_SUFFIX[resolveEventPosition(node)];
  return suffix ? `${type} ${suffix}` : type;
}

export type GlyphKind =
  | { kind: "gateway"; gateway: GatewayType }
  | { kind: "event"; event: EventType; position: EventPosition }
  | { kind: "task"; task: TaskType }
  | { kind: "trigger" }
  | { kind: "legacy"; type: string };

/**
 * Resolve the canvas marker to draw for a node — a gateway/event BPMN marker
 * (rendered by glyphs.tsx) or a task-corner lucide icon.
 */
export function resolveGlyph(node: NodeLike): GlyphKind {
  const category = nodeCategory(node.type);
  if (category === "trigger") return { kind: "trigger" };
  if (category === "gateway") {
    if (node.type === "gateway") return { kind: "gateway", gateway: resolveGatewayType(node) };
    return { kind: "legacy", type: node.type ?? "" };
  }
  if (category === "event") {
    if (node.type === "delay") return { kind: "event", event: "timer", position: "intermediate" };
    return { kind: "event", event: resolveEventType(node), position: resolveEventPosition(node) };
  }
  // activity
  if (node.type === "task") return { kind: "task", task: resolveTaskType(node) };
  return { kind: "legacy", type: node.type ?? "" };
}

export function isBoundaryEvent(node: NodeLike): boolean {
  return node.type === NODE_EVENT && resolveEventPosition(node) === "boundary";
}

export function isEndEvent(node: NodeLike): boolean {
  return node.type === NODE_EVENT && resolveEventPosition(node) === "end";
}

// --------------------------------------------------------------------------- //
// Handle resolution (single source of truth for every node's handles)
// --------------------------------------------------------------------------- //
interface SwitchCaseLike {
  handle: string;
  label?: string;
}

function evenOffset(index: number, count: number): number {
  return (index + 0.5) / count;
}

/**
 * The handle spec for a node, derived from its type + data. Preserves the exact
 * handle ids legacy graphs already use ("true"/"false", `case-*`/"default") so
 * stored edges keep landing on the right handle after the visual refactor.
 */
export function handlesFor(node: NodeLike): HandleSpec[] {
  const category = nodeCategory(node.type);
  const data = asData(node);

  if (category === "trigger") {
    return [{ type: "source", position: Position.Bottom, variant: "neutral" }];
  }

  if (category === "event") {
    if (node.type !== "delay") {
      const pos = resolveEventPosition(node);
      if (pos === "end") return [{ type: "target", position: Position.Top, variant: "neutral" }];
      if (pos === "boundary") return [{ type: "source", position: Position.Bottom, variant: "boundary" }];
    }
    return [
      { type: "target", position: Position.Top, variant: "neutral" },
      { type: "source", position: Position.Bottom, variant: "neutral" },
    ];
  }

  if (category === "activity") {
    return [
      { type: "target", position: Position.Top, variant: "neutral" },
      { type: "source", position: Position.Bottom, variant: "neutral" },
    ];
  }

  // gateway family (gateway/condition/switch/merge/passthrough)
  const target: HandleSpec = { type: "target", position: Position.Top, variant: "neutral" };
  const cases = Array.isArray(data.cases) ? (data.cases as SwitchCaseLike[]) : [];

  if (node.type === "switch" || (node.type === "gateway" && cases.length > 0)) {
    const handles = [...cases, { handle: HANDLE_DEFAULT, label: "default" }];
    const count = handles.length;
    return [
      target,
      ...handles.map((c, i) => ({
        id: c.handle,
        type: "source" as const,
        position: Position.Bottom,
        offset: evenOffset(i, count),
        label: c.label || (c.handle === HANDLE_DEFAULT ? "default" : `Case ${i + 1}`),
        variant: (c.handle === HANDLE_DEFAULT ? "default" : "neutral") as HandleVariant,
      })),
    ];
  }

  const branchesTrueFalse =
    node.type === "condition" ||
    (node.type === "gateway" && resolveGatewayType(node) === "exclusive" && data.expr != null);
  if (branchesTrueFalse) {
    return [
      target,
      { id: HANDLE_TRUE, type: "source", position: Position.Bottom, offset: 0.25, label: "true", variant: "true" },
      { id: HANDLE_FALSE, type: "source", position: Position.Bottom, offset: 0.75, label: "false", variant: "false" },
    ];
  }

  return [target, { type: "source", position: Position.Bottom, variant: "neutral" }];
}
