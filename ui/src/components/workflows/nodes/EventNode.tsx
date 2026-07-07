"use client";

import { type NodeProps } from "@xyflow/react";

import { cn } from "@/lib/utils";

import {
  NodeHandles,
  ProblemBadge,
  statusRingClass,
  useNodeChrome,
} from "./BaseNode";
import { BoundaryEventNode } from "./BoundaryEventNode";
import { EventGlyph } from "./glyphs";
import { ACCENTS, handlesFor, resolveEventPosition, resolveEventType, subtypeLabel } from "./nodeMeta";

/**
 * Event = circle whose ring encodes its position: thin=start, double ring=
 * intermediate (catch/throw), thick=end. Boundary events delegate to
 * {@link BoundaryEventNode}. The inner SVG glyph encodes the event_type.
 */
export function EventNode(props: NodeProps) {
  const { id, data, selected } = props;
  const node = { type: "event", data: data as Record<string, unknown> };
  const position = resolveEventPosition(node);
  const chrome = useNodeChrome(id);

  if (position === "boundary") return <BoundaryEventNode {...props} />;

  const eventType = resolveEventType(node);
  const ringWidth = position === "end" ? "border-[3px]" : "border-2";

  return (
    <div className="relative flex flex-col items-center">
      <div
        className={cn(
          "relative flex h-12 w-12 items-center justify-center rounded-full bg-card shadow-sm transition-colors",
          ringWidth,
          selected ? "border-primary" : ACCENTS.indigo.border,
          statusRingClass(chrome.status),
        )}
      >
        {/* Double ring marks an intermediate (catch/throw) event. */}
        {position === "intermediate" ? (
          <span className={cn("absolute inset-1 rounded-full border-2", ACCENTS.indigo.border)} />
        ) : null}
        <span className={ACCENTS.indigo.text}>
          <EventGlyph type={eventType} className="h-5 w-5" />
        </span>
      </div>
      <span className="pointer-events-none whitespace-nowrap pt-0.5 text-[10px] font-medium text-muted-foreground">
        {subtypeLabel(node)}
      </span>
      <ProblemBadge severity={chrome.problem ?? null} count={chrome.problemCount} />
      <NodeHandles handles={handlesFor(node)} accent="indigo" />
    </div>
  );
}
