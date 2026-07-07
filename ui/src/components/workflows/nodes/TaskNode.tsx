"use client";

import { type NodeProps } from "@xyflow/react";

import { ACTION_LABELS } from "@/components/workflows/actionTypes";

import { BaseNode, useNodeChrome } from "./BaseNode";
import {
  handlesFor,
  resolveTaskType,
  subtypeLabel,
  TASK_ICONS,
  TASK_LABELS,
  WAIT_TASK_TYPES,
} from "./nodeMeta";

export function TaskNode({ id, data, selected }: NodeProps) {
  const node = { type: "task", data: data as Record<string, unknown> };
  const taskType = resolveTaskType(node);
  const Icon = TASK_ICONS[taskType];
  const actionType = (data?.action_type as string | undefined) ?? "";
  const chrome = useNodeChrome(id);

  const sublabel = WAIT_TASK_TYPES.includes(taskType)
    ? `waits for ${TASK_LABELS[taskType].toLowerCase()} completion`
    : actionType
      ? (ACTION_LABELS[actionType] ?? actionType)
      : "no action set";

  return (
    <BaseNode
      accent="sky"
      label={subtypeLabel(node)}
      glyph={<Icon className="h-4 w-4" />}
      sublabel={sublabel}
      selected={selected}
      handles={handlesFor(node)}
      status={chrome.status}
      problem={chrome.problem}
      problemCount={chrome.problemCount}
    />
  );
}
