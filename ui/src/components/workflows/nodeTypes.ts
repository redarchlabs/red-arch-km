import { LabeledEdge } from "@/components/workflows/designer/LabeledEdge";
import { ActionNode } from "@/components/workflows/nodes/ActionNode";
import { BoundaryEventNode } from "@/components/workflows/nodes/BoundaryEventNode";
import { ConditionNode } from "@/components/workflows/nodes/ConditionNode";
import { DelayNode } from "@/components/workflows/nodes/DelayNode";
import { EventNode } from "@/components/workflows/nodes/EventNode";
import { GatewayNode } from "@/components/workflows/nodes/GatewayNode";
import { GenericNode } from "@/components/workflows/nodes/GenericNode";
import { SwitchNode } from "@/components/workflows/nodes/SwitchNode";
import { TaskNode } from "@/components/workflows/nodes/TaskNode";
import { TriggerNode } from "@/components/workflows/nodes/TriggerNode";

/** Shared React Flow node/edge type registries — used by the editable designer
 * canvas and the read-only run overlay so both render nodes identically. */
export const NODE_TYPES = {
  trigger: TriggerNode,
  task: TaskNode,
  gateway: GatewayNode,
  event: EventNode,
  boundaryEvent: BoundaryEventNode,
  action: ActionNode,
  condition: ConditionNode,
  switch: SwitchNode,
  delay: DelayNode,
  merge: GenericNode,
  passthrough: GenericNode,
} as const;

export const EDGE_TYPES = { labeled: LabeledEdge } as const;
