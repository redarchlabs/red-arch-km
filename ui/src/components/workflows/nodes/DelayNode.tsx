"use client";

import { type NodeProps } from "@xyflow/react";
import { Clock } from "lucide-react";

import { BaseNode, useNodeChrome } from "./BaseNode";
import { handlesFor } from "./nodeMeta";

export function DelayNode({ id, data, selected }: NodeProps) {
  const amount = Number(data?.delay_amount ?? 0);
  const unit = (data?.delay_unit as string | undefined) ?? "minutes";
  const seconds = Number(data?.delay_seconds ?? 0);
  const label = seconds > 0 ? `${amount} ${unit}` : "no delay set";
  const chrome = useNodeChrome(id);

  return (
    <BaseNode
      accent="slate"
      label="Delay"
      glyph={<Clock className="h-4 w-4" />}
      sublabel={`wait ${label}`}
      selected={selected}
      minWidth={160}
      handles={handlesFor({ type: "delay", data: data as Record<string, unknown> })}
      status={chrome.status}
      problem={chrome.problem}
      problemCount={chrome.problemCount}
    />
  );
}
