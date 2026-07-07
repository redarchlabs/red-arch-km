"use client";

import { type NodeProps } from "@xyflow/react";
import { Zap } from "lucide-react";

import { BaseNode, useNodeChrome } from "./BaseNode";
import { handlesFor } from "./nodeMeta";

export function TriggerNode({ id, data, selected }: NodeProps) {
  const operations = (data?.operations as string[] | undefined) ?? ["create", "update", "delete"];
  const fields = (data?.field_filter as string[] | undefined) ?? [];
  const chrome = useNodeChrome(id);
  const sublabel = `on ${operations.join(", ")}${fields.length > 0 ? ` · fields: ${fields.join(", ")}` : ""}`;
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
