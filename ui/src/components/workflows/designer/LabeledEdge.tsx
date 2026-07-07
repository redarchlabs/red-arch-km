"use client";

/**
 * Edge renderer that labels branch edges (a gateway/condition/switch out-edge)
 * with a short, human summary derived from the source node — `true`/`else`, a
 * switch case label, or a `describeExpr` summary of the branch condition. Keeps
 * the red stroke for false/error branches so the unhappy path reads at a glance.
 */
import { BaseEdge, EdgeLabelRenderer, getBezierPath, type EdgeProps } from "@xyflow/react";

import { describeExpr } from "@/components/workflows/conditionExpr";

import { useDesignerStore } from "./store";

interface SwitchCase {
  handle: string;
  label?: string;
  expr?: unknown;
}

const RED = "#f43f5e";

function branchLabel(
  sourceNode: { type?: string; data?: Record<string, unknown> } | undefined,
  handle: string | null | undefined,
): string {
  if (!handle) return "";
  if (handle === "true") {
    const summary = describeExpr(sourceNode?.data?.expr);
    return summary || "true";
  }
  if (handle === "false") return "else";
  if (handle === "default") return "else";
  if (handle === "error") return "error";
  if (handle === "boundary") return "escape";
  if (handle.startsWith("case-")) {
    const cases = Array.isArray(sourceNode?.data?.cases) ? (sourceNode?.data?.cases as SwitchCase[]) : [];
    const match = cases.find((c) => c.handle === handle);
    if (match) return match.label || describeExpr(match.expr) || "case";
    return "case";
  }
  return handle;
}

export function LabeledEdge({
  id,
  source,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  sourceHandleId,
  markerEnd,
  style,
  selected,
}: EdgeProps) {
  const [path, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  });

  const sourceNode = useDesignerStore((s) => s.nodes.find((n) => n.id === source));
  const label = branchLabel(sourceNode, sourceHandleId);
  const isRed = sourceHandleId === "false" || sourceHandleId === "error";
  const edgeStyle = { ...style, ...(isRed ? { stroke: RED } : {}), ...(selected ? { strokeWidth: 2 } : {}) };

  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd} style={edgeStyle} />
      {label ? (
        <EdgeLabelRenderer>
          <div
            className="pointer-events-none absolute rounded bg-background/90 px-1 text-[10px] font-medium text-muted-foreground shadow-sm"
            style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
          >
            {label}
          </div>
        </EdgeLabelRenderer>
      ) : null}
    </>
  );
}
