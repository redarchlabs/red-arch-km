"use client";

import { type NodeProps } from "@xyflow/react";

import { cn } from "@/lib/utils";

import {
  NodeHandles,
  ProblemBadge,
  statusRingClass,
  useNodeChrome,
} from "./BaseNode";
import { EventGlyph } from "./glyphs";
import { ACCENTS, handlesFor, resolveEventType, subtypeLabel } from "./nodeMeta";

/**
 * A boundary event — a small circle that rides its host activity's border
 * (positioned by React Flow via `parentId`/`extent`, added in serde). Solid ring
 * = interrupting (cancels the host); dashed ring = non-interrupting.
 */
export function BoundaryEventNode({ id, data, selected }: NodeProps) {
  const node = { type: "event", data: data as Record<string, unknown> };
  const eventType = resolveEventType(node);
  const interrupting = data?.interrupting !== false;
  const chrome = useNodeChrome(id);

  return (
    <div className="relative flex flex-col items-center">
      <div
        className={cn(
          "flex h-7 w-7 items-center justify-center rounded-full border-2 bg-card shadow-sm transition-colors",
          interrupting ? "border-solid" : "border-dashed",
          selected ? "border-primary" : ACCENTS.rose.border,
          statusRingClass(chrome.status),
        )}
      >
        <span className={ACCENTS.rose.text}>
          <EventGlyph type={eventType} className="h-3.5 w-3.5" />
        </span>
      </div>
      <span className="pointer-events-none whitespace-nowrap pt-0.5 text-[9px] font-medium text-muted-foreground">
        {subtypeLabel(node)}
      </span>
      <ProblemBadge severity={chrome.problem ?? null} count={chrome.problemCount} />
      <NodeHandles handles={handlesFor(node)} accent="rose" />
    </div>
  );
}
