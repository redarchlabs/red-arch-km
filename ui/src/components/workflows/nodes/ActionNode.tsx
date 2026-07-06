"use client";

import { Handle, Position, type NodeProps } from "@xyflow/react";
import { Bolt } from "lucide-react";

import { cn } from "@/lib/utils";
import { ACTION_LABELS } from "@/components/workflows/actionTypes";

export function ActionNode({ data, selected }: NodeProps) {
  const actionType = (data?.action_type as string | undefined) ?? "";
  const label = ACTION_LABELS[actionType] ?? actionType ?? "Choose action";
  return (
    <div
      className={cn(
        "min-w-[180px] rounded-lg border-2 bg-card px-3 py-2 shadow-sm transition-colors",
        selected ? "border-primary" : "border-sky-500/60",
      )}
    >
      <Handle type="target" position={Position.Top} className="!h-2.5 !w-2.5 !bg-sky-500" />
      <div className="flex items-center gap-2">
        <Bolt className="h-4 w-4 text-sky-500" />
        <span className="text-sm font-semibold">Action</span>
      </div>
      <div className="mt-1 text-xs text-muted-foreground">{label}</div>
      <Handle type="source" position={Position.Bottom} className="!h-2.5 !w-2.5 !bg-sky-500" />
    </div>
  );
}
