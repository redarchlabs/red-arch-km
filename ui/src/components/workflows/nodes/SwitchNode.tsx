"use client";

import { type NodeProps } from "@xyflow/react";
import { Split } from "lucide-react";

import { BaseNode, useNodeChrome } from "./BaseNode";
import { handlesFor } from "./nodeMeta";

export function SwitchNode({ id, data, selected }: NodeProps) {
  const handles = handlesFor({ type: "switch", data: data as Record<string, unknown> });
  const sources = handles.filter((h) => h.type === "source");
  const chrome = useNodeChrome(id);

  return (
    <BaseNode
      accent="violet"
      label="Switch"
      glyph={<Split className="h-4 w-4" />}
      selected={selected}
      minWidth={220}
      className="pb-4"
      handles={handles}
      status={chrome.status}
      problem={chrome.problem}
      problemCount={chrome.problemCount}
    >
      <div className="mt-2 flex justify-between gap-1 text-[10px] font-medium">
        {sources.map((h) => (
          <span
            key={h.id}
            className="max-w-[70px] truncate text-violet-600 dark:text-violet-300"
            title={h.label}
          >
            {h.label}
          </span>
        ))}
      </div>
    </BaseNode>
  );
}
