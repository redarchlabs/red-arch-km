"use client";

/**
 * Shared node shell. Rectangular nodes (trigger/task/action/condition/switch/
 * delay) compose {@link BaseNode}; the shaped nodes (event circle, gateway
 * diamond) reuse the exported slots — {@link NodeHandles}, {@link StatusRing},
 * {@link ProblemBadge} — directly. Purely presentational: it never touches a
 * node's `data` or the serde output.
 */
import { Handle, type NodeProps } from "@xyflow/react";
import { createContext, useContext, type CSSProperties, type ReactNode } from "react";

import { cn } from "@/lib/utils";

import {
  ACCENTS,
  HANDLE_VARIANT_ACCENT,
  type AccentName,
  type HandleSpec,
} from "./nodeMeta";

/** Live-run status for the (later) status ring — kept minimal for Phase 0. */
export type NodeRunStatus = "idle" | "waiting" | "active" | "completed" | "failed";

/** Transient, per-node chrome (validation badge + live-run ring) supplied by the
 * designer through context so it never leaks into the node's serialisable `data`. */
export interface NodeChrome {
  status?: NodeRunStatus;
  problem?: "error" | "warning" | null;
  problemCount?: number;
}

export const NodeChromeContext = createContext<Record<string, NodeChrome>>({});

export function useNodeChrome(id: string): NodeChrome {
  return useContext(NodeChromeContext)[id] ?? {};
}

const STATUS_RING: Record<Exclude<NodeRunStatus, "idle">, string> = {
  waiting: "ring-2 ring-amber-400/70",
  active: "ring-2 ring-sky-400 animate-pulse",
  completed: "ring-2 ring-emerald-400/70",
  failed: "ring-2 ring-rose-500/80",
};

/** Ring the node when a run touches it. Renders nothing at rest. */
export function statusRingClass(status: NodeRunStatus | undefined): string {
  if (!status || status === "idle") return "";
  return STATUS_RING[status];
}

interface HandlesProps {
  handles: HandleSpec[];
  /** default accent for neutral handles (falls back to slate). */
  accent?: AccentName;
}

/** Render every {@link HandleSpec} for a node as a React Flow <Handle>. */
export function NodeHandles({ handles, accent }: HandlesProps) {
  return (
    <>
      {handles.map((h, i) => {
        const variantAccent = h.variant && h.variant !== "neutral" ? HANDLE_VARIANT_ACCENT[h.variant] : accent ?? "slate";
        const style: CSSProperties = {};
        if (h.offset != null) {
          const horizontal = h.position === "top" || h.position === "bottom";
          if (horizontal) style.left = `${h.offset * 100}%`;
          else style.top = `${h.offset * 100}%`;
        }
        return (
          <Handle
            key={h.id ?? `${h.type}-${h.position}-${i}`}
            id={h.id}
            type={h.type}
            position={h.position}
            style={style}
            className={cn("!h-2.5 !w-2.5", ACCENTS[variantAccent].handle)}
          />
        );
      })}
    </>
  );
}

interface ProblemBadgeProps {
  /** highest severity among the node's issues, or null when clean. */
  severity: "error" | "warning" | null;
  count?: number;
  className?: string;
}

/** Corner badge flagging validation issues on a node. Renders nothing when clean. */
export function ProblemBadge({ severity, count, className }: ProblemBadgeProps) {
  if (!severity) return null;
  return (
    <span
      className={cn(
        "absolute -right-1.5 -top-1.5 flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-[10px] font-bold text-white shadow",
        severity === "error" ? "bg-rose-500" : "bg-amber-500",
        className,
      )}
      title={severity === "error" ? "Has errors" : "Has warnings"}
      aria-label={severity === "error" ? "Node has errors" : "Node has warnings"}
    >
      {count && count > 1 ? count : "!"}
    </span>
  );
}

interface BaseNodeProps extends Pick<NodeProps, "selected"> {
  glyph?: ReactNode;
  label: string;
  sublabel?: ReactNode;
  accent: AccentName;
  handles: HandleSpec[];
  status?: NodeRunStatus;
  problem?: "error" | "warning" | null;
  problemCount?: number;
  children?: ReactNode;
  className?: string;
  minWidth?: number;
}

/** The rounded-rectangle node shell (header glyph + label + body + handles). */
export function BaseNode({
  glyph,
  label,
  sublabel,
  accent,
  handles,
  selected,
  status,
  problem,
  problemCount,
  children,
  className,
  minWidth = 180,
}: BaseNodeProps) {
  const a = ACCENTS[accent];
  return (
    <div
      style={{ minWidth }}
      className={cn(
        "relative rounded-lg border-2 bg-card px-3 py-2 shadow-sm transition-colors",
        selected ? "border-primary" : a.border,
        statusRingClass(status),
        className,
      )}
    >
      <ProblemBadge severity={problem ?? null} count={problemCount} />
      <div className="flex items-center gap-2">
        {glyph ? <span className={cn("flex h-4 w-4 items-center justify-center", a.text)}>{glyph}</span> : null}
        <span className="text-sm font-semibold">{label}</span>
      </div>
      {sublabel != null ? <div className="mt-1 text-xs text-muted-foreground">{sublabel}</div> : null}
      {children}
      <NodeHandles handles={handles} accent={accent} />
    </div>
  );
}
