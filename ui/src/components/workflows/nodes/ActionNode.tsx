"use client";

import { type NodeProps } from "@xyflow/react";
import { Bolt } from "lucide-react";

import { ACTION_LABELS } from "@/components/workflows/actionTypes";

import { BaseNode, useNodeChrome } from "./BaseNode";
import { handlesFor } from "./nodeMeta";

export function ActionNode({ id, data, selected }: NodeProps) {
  const actionType = (data?.action_type as string | undefined) ?? "";
  const label = ACTION_LABELS[actionType] ?? actionType ?? "Choose action";
  const chrome = useNodeChrome(id);
  return (
    <BaseNode
      accent="sky"
      label="Action"
      glyph={<Bolt className="h-4 w-4" />}
      sublabel={label}
      selected={selected}
      handles={handlesFor({ type: "action", data: data as Record<string, unknown> })}
      status={chrome.status}
      problem={chrome.problem}
      problemCount={chrome.problemCount}
    />
  );
}
