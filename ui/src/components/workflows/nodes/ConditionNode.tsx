"use client";

import { type NodeProps } from "@xyflow/react";
import { GitBranch } from "lucide-react";

import { describeExpr } from "@/components/workflows/conditionExpr";

import { BaseNode, useNodeChrome } from "./BaseNode";
import { handlesFor } from "./nodeMeta";

export function ConditionNode({ id, data, selected }: NodeProps) {
  const summary = describeExpr(data?.expr) || "Always";
  const chrome = useNodeChrome(id);
  return (
    <BaseNode
      accent="amber"
      label="Condition"
      glyph={<GitBranch className="h-4 w-4" />}
      selected={selected}
      minWidth={200}
      handles={handlesFor({ type: "condition", data: data as Record<string, unknown> })}
      status={chrome.status}
      problem={chrome.problem}
      problemCount={chrome.problemCount}
    >
      <div className="mt-1 max-w-[220px] truncate text-xs text-muted-foreground" title={summary}>
        {summary}
      </div>
      <div className="mt-2 flex justify-between text-[10px] font-medium">
        <span className="text-emerald-600 dark:text-emerald-400">true</span>
        <span className="text-rose-600 dark:text-rose-400">false</span>
      </div>
    </BaseNode>
  );
}
