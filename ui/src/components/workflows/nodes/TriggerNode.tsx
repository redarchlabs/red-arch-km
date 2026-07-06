"use client";

import { Handle, Position, type NodeProps } from "@xyflow/react";
import { Zap } from "lucide-react";

import { cn } from "@/lib/utils";

export function TriggerNode({ data, selected }: NodeProps) {
  const operations = (data?.operations as string[] | undefined) ?? ["create", "update", "delete"];
  const fields = (data?.field_filter as string[] | undefined) ?? [];
  return (
    <div
      className={cn(
        "min-w-[180px] rounded-lg border-2 bg-card px-3 py-2 shadow-sm transition-colors",
        selected ? "border-primary" : "border-emerald-500/60",
      )}
    >
      <div className="flex items-center gap-2">
        <Zap className="h-4 w-4 text-emerald-500" />
        <span className="text-sm font-semibold">Trigger</span>
      </div>
      <div className="mt-1 text-xs text-muted-foreground">
        on {operations.join(", ")}
        {fields.length > 0 ? ` · fields: ${fields.join(", ")}` : ""}
      </div>
      <Handle type="source" position={Position.Bottom} className="!h-2.5 !w-2.5 !bg-emerald-500" />
    </div>
  );
}
