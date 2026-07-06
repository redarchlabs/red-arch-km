"use client";

import { Handle, Position, type NodeProps } from "@xyflow/react";
import { GitBranch } from "lucide-react";

import { cn } from "@/lib/utils";
import { describeExpr } from "@/components/workflows/conditionExpr";

export function ConditionNode({ data, selected }: NodeProps) {
  const summary = describeExpr(data?.expr) || "Always";
  return (
    <div
      className={cn(
        "min-w-[200px] rounded-lg border-2 bg-card px-3 py-2 shadow-sm transition-colors",
        selected ? "border-primary" : "border-amber-500/60",
      )}
    >
      <Handle type="target" position={Position.Top} className="!h-2.5 !w-2.5 !bg-amber-500" />
      <div className="flex items-center gap-2">
        <GitBranch className="h-4 w-4 text-amber-500" />
        <span className="text-sm font-semibold">Condition</span>
      </div>
      <div className="mt-1 max-w-[220px] truncate text-xs text-muted-foreground" title={summary}>
        {summary}
      </div>
      <div className="mt-2 flex justify-between text-[10px] font-medium">
        <span className="text-emerald-600 dark:text-emerald-400">true</span>
        <span className="text-rose-600 dark:text-rose-400">false</span>
      </div>
      {/* Two source handles: left=true, right=false. */}
      <Handle
        id="true"
        type="source"
        position={Position.Bottom}
        style={{ left: "25%" }}
        className="!h-2.5 !w-2.5 !bg-emerald-500"
      />
      <Handle
        id="false"
        type="source"
        position={Position.Bottom}
        style={{ left: "75%" }}
        className="!h-2.5 !w-2.5 !bg-rose-500"
      />
    </div>
  );
}
