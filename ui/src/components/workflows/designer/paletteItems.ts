/**
 * The categorized palette — the draggable BPMN vocabulary. Pure data derived
 * from the node registry so the palette, drop-to-place and command palette all
 * offer the same set. `makeData` returns the starter `data` (with the concrete
 * subtype baked in) for a dropped node.
 */
import {
  ArrowUp,
  CircleStop,
  Clock,
  Diamond,
  Dot,
  Mail,
  Plus,
  Radio,
  TriangleAlert,
  X,
  Zap,
  type LucideIcon,
} from "lucide-react";

import {
  GATEWAY_LABELS,
  TASK_ICONS,
  TASK_LABELS,
  type Category,
  type GatewayType,
  type TaskType,
} from "@/components/workflows/nodes/nodeMeta";

export interface PaletteItem {
  key: string;
  /** node `type` (BPMN category) the item drops. */
  type: string;
  label: string;
  icon: LucideIcon;
  hint?: string;
  makeData: () => Record<string, unknown>;
}

export interface PaletteGroup {
  category: Category;
  label: string;
  items: PaletteItem[];
}

const taskItem = (task: TaskType): PaletteItem => ({
  key: `task:${task}`,
  type: "task",
  label: `${TASK_LABELS[task]} task`,
  icon: TASK_ICONS[task],
  makeData: () => ({ task_type: task, action_type: "", config: {} }),
});

const GATEWAY_ICONS: Record<GatewayType, LucideIcon> = {
  exclusive: X,
  parallel: Plus,
  inclusive: Radio,
  event_based: Diamond,
};

const gatewayItem = (gateway: GatewayType): PaletteItem => ({
  key: `gateway:${gateway}`,
  type: "gateway",
  label: `${GATEWAY_LABELS[gateway]} gateway`,
  icon: GATEWAY_ICONS[gateway],
  makeData: () => ({ gateway_type: gateway }),
});

export const PALETTE_GROUPS: PaletteGroup[] = [
  {
    category: "trigger",
    label: "Start",
    items: [
      {
        key: "trigger",
        type: "trigger",
        label: "Trigger",
        icon: Zap,
        hint: "Starts a run on a record change or schedule",
        makeData: () => ({ operations: ["update"], field_filter: [] }),
      },
    ],
  },
  {
    category: "activity",
    label: "Activities",
    items: [
      taskItem("service"),
      taskItem("send"),
      taskItem("user"),
      taskItem("receive"),
      taskItem("script"),
      taskItem("businessRule"),
      taskItem("call"),
      taskItem("subProcess"),
      taskItem("manual"),
    ],
  },
  {
    category: "gateway",
    label: "Gateways",
    items: [gatewayItem("exclusive"), gatewayItem("parallel"), gatewayItem("inclusive"), gatewayItem("event_based")],
  },
  {
    category: "event",
    label: "Events",
    items: [
      { key: "event:end", type: "event", label: "End", icon: Dot, makeData: () => ({ position: "end", event_type: "none" }) },
      { key: "event:terminate", type: "event", label: "Terminate end", icon: CircleStop, makeData: () => ({ position: "end", event_type: "terminate" }) },
      { key: "event:timer", type: "event", label: "Timer", icon: Clock, makeData: () => ({ position: "intermediate", event_type: "timer", throw_catch: "catch" }) },
      { key: "event:message", type: "event", label: "Message", icon: Mail, makeData: () => ({ position: "intermediate", event_type: "message", throw_catch: "catch" }) },
      { key: "event:signal", type: "event", label: "Signal", icon: Radio, makeData: () => ({ position: "intermediate", event_type: "signal", throw_catch: "catch" }) },
      { key: "event:error", type: "event", label: "Error end", icon: TriangleAlert, makeData: () => ({ position: "end", event_type: "error" }) },
      { key: "event:escalation", type: "event", label: "Escalation", icon: ArrowUp, makeData: () => ({ position: "intermediate", event_type: "escalation", throw_catch: "throw" }) },
    ],
  },
];

/** Flat list (command palette / lookups). */
export const PALETTE_ITEMS: PaletteItem[] = PALETTE_GROUPS.flatMap((g) => g.items);
