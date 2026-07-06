"use client";

import { Handle, Position, type NodeProps } from "@xyflow/react";
import { Clock } from "lucide-react";

import { cn } from "@/lib/utils";

export function DelayNode({ data, selected }: NodeProps) {
  const amount = Number(data?.delay_amount ?? 0);
  const unit = (data?.delay_unit as string | undefined) ?? "minutes";
  const seconds = Number(data?.delay_seconds ?? 0);
  const label = seconds > 0 ? `${amount} ${unit}` : "no delay set";

  return (
    <div
      className={cn(
        "min-w-[160px] rounded-lg border-2 bg-card px-3 py-2 shadow-sm transition-colors",
        selected ? "border-primary" : "border-slate-400/70",
      )}
    >
      <Handle type="target" position={Position.Top} className="!h-2.5 !w-2.5 !bg-slate-400" />
      <div className="flex items-center gap-2">
        <Clock className="h-4 w-4 text-slate-500" />
        <span className="text-sm font-semibold">Delay</span>
      </div>
      <div className="mt-1 text-xs text-muted-foreground">wait {label}</div>
      <Handle type="source" position={Position.Bottom} className="!h-2.5 !w-2.5 !bg-slate-400" />
    </div>
  );
}
