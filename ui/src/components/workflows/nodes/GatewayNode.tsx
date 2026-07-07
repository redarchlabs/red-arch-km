"use client";

import { type NodeProps } from "@xyflow/react";

import { cn } from "@/lib/utils";

import {
  NodeHandles,
  ProblemBadge,
  statusRingClass,
  useNodeChrome,
} from "./BaseNode";
import { GatewayGlyph } from "./glyphs";
import { ACCENTS, handlesFor, resolveGatewayType, subtypeLabel } from "./nodeMeta";

/** Gateway = diamond with an X/+/O/pentagon marker and a word label beneath. */
export function GatewayNode({ id, data, selected }: NodeProps) {
  const node = { type: "gateway", data: data as Record<string, unknown> };
  const gatewayType = resolveGatewayType(node);
  const handles = handlesFor(node);
  const chrome = useNodeChrome(id);

  return (
    <div className="relative flex items-center justify-center" style={{ width: 56, height: 56 }}>
      <div
        className={cn(
          "flex h-10 w-10 rotate-45 items-center justify-center rounded-md border-2 bg-card shadow-sm transition-colors",
          selected ? "border-primary" : ACCENTS.amber.border,
          statusRingClass(chrome.status),
        )}
      >
        <span className={cn("-rotate-45", ACCENTS.amber.text)}>
          <GatewayGlyph type={gatewayType} className="h-5 w-5" />
        </span>
      </div>
      <span className="pointer-events-none absolute left-1/2 top-full -translate-x-1/2 whitespace-nowrap pt-0.5 text-[10px] font-medium text-muted-foreground">
        {subtypeLabel(node)}
      </span>
      <ProblemBadge severity={chrome.problem ?? null} count={chrome.problemCount} />
      <NodeHandles handles={handles} accent="amber" />
    </div>
  );
}
