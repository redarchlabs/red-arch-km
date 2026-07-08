"use client";

import { type NodeProps } from "@xyflow/react";
import { Zap } from "lucide-react";

import { BaseNode, useNodeChrome } from "./BaseNode";
import { handlesFor } from "./nodeMeta";

/** Human-readable summary of what starts this workflow, shown under the node. */
function triggerSublabel(source: string, data: Record<string, unknown> | undefined): string {
  if (source === "manual") {
    const inputs = (data?.inputs as unknown[] | undefined) ?? [];
    const n = inputs.length;
    return n > 0 ? `runs on demand · ${n} input${n === 1 ? "" : "s"}` : "runs on demand";
  }
  const operations = (data?.operations as string[] | undefined) ?? ["create", "update", "delete"];
  const fields = (data?.field_filter as string[] | undefined) ?? [];
  const scope = source === "form" ? "form submissions" : operations.join(", ");
  const base = operations.length === 0 && source !== "form" ? "on a schedule" : `on ${scope}`;
  return `${base}${fields.length > 0 ? ` · fields: ${fields.join(", ")}` : ""}`;
}

export function TriggerNode({ id, data, selected }: NodeProps) {
  const source = (data?.source as string | undefined) ?? "any";
  const chrome = useNodeChrome(id);
  const sublabel = triggerSublabel(source, data as Record<string, unknown> | undefined);
  return (
    <BaseNode
      accent="emerald"
      label="Trigger"
      glyph={<Zap className="h-4 w-4" />}
      sublabel={sublabel}
      selected={selected}
      handles={handlesFor({ type: "trigger", data: data as Record<string, unknown> })}
      status={chrome.status}
      problem={chrome.problem}
      problemCount={chrome.problemCount}
    />
  );
}
