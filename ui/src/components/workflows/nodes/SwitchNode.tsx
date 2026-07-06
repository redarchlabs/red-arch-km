"use client";

import { Handle, Position, type NodeProps } from "@xyflow/react";
import { Split } from "lucide-react";

import { cn } from "@/lib/utils";

interface SwitchCase {
  handle: string;
  label?: string;
}

export function SwitchNode({ data, selected }: NodeProps) {
  const cases = (data?.cases as SwitchCase[] | undefined) ?? [];
  // One source handle per case, plus a trailing "default" handle.
  const handles = [
    ...cases.map((c, i) => ({ id: c.handle, label: c.label || `Case ${i + 1}` })),
    { id: "default", label: "default" },
  ];
  const count = handles.length;

  return (
    <div
      className={cn(
        "min-w-[220px] rounded-lg border-2 bg-card px-3 py-2 pb-4 shadow-sm transition-colors",
        selected ? "border-primary" : "border-violet-500/60",
      )}
    >
      <Handle type="target" position={Position.Top} className="!h-2.5 !w-2.5 !bg-violet-500" />
      <div className="flex items-center gap-2">
        <Split className="h-4 w-4 text-violet-500" />
        <span className="text-sm font-semibold">Switch</span>
      </div>
      <div className="mt-2 flex justify-between gap-1 text-[10px] font-medium">
        {handles.map((h) => (
          <span key={h.id} className="max-w-[70px] truncate text-violet-600 dark:text-violet-300" title={h.label}>
            {h.label}
          </span>
        ))}
      </div>
      {handles.map((h, i) => (
        <Handle
          key={h.id}
          id={h.id}
          type="source"
          position={Position.Bottom}
          style={{ left: `${((i + 0.5) / count) * 100}%` }}
          className="!h-2.5 !w-2.5 !bg-violet-500"
        />
      ))}
    </div>
  );
}
