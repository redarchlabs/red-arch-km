"use client";

import {
  AlertTriangle,
  CheckCircle2,
  CornerDownRight,
  MessageSquare,
  PlayCircle,
  Reply,
  UserCheck,
} from "lucide-react";
import { useMemo } from "react";

import type { WorkOrderEventKind, WorkOrderMap } from "@/lib/api/workOrders";
import { cn } from "@/lib/utils";
import { EVENT_WIDTH, LANE_HEIGHT, laneCenterY, layoutLanes } from "@/lib/workOrderLanes";

const LABEL_WIDTH = 168;

const KIND_META: Record<
  WorkOrderEventKind,
  { Icon: typeof PlayCircle; tone: string; label: string }
> = {
  started: { Icon: PlayCircle, tone: "text-blue-600", label: "started" },
  delegated: { Icon: CornerDownRight, tone: "text-violet-600", label: "delegated" },
  consulted: { Icon: MessageSquare, tone: "text-amber-600", label: "consulted" },
  answered: { Icon: Reply, tone: "text-emerald-600", label: "answered" },
  blocked: { Icon: UserCheck, tone: "text-amber-600", label: "blocked" },
  finished: { Icon: CheckCircle2, tone: "text-emerald-600", label: "finished" },
  failed: { Icon: AlertTriangle, tone: "text-destructive", label: "failed" },
  note: { Icon: MessageSquare, tone: "text-muted-foreground", label: "note" },
};

/** Rolled-up lane state, shown against the agent's name. `waiting` is amber
 *  rather than green because a waiting lane is not progressing. */
const LANE_TONE: Record<string, string> = {
  done: "bg-emerald-500",
  running: "bg-blue-500 animate-pulse",
  queued: "bg-slate-400",
  waiting: "bg-amber-500",
  error: "bg-destructive",
  cancelled: "bg-muted-foreground",
};

const TIME = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" });

interface AgentSwimLanesProps {
  map: WorkOrderMap;
}

/**
 * One lane per participant, events placed on a shared clock.
 *
 * Cross-lane arrows are drawn in a single SVG behind the pills so a consult
 * visibly leaves one lane and lands in another — which is what distinguishes an
 * agent that is blocked on a peer from one that simply stopped.
 */
export function AgentSwimLanes({ map }: AgentSwimLanesProps) {
  const { placed, width, height } = useMemo(
    () => layoutLanes(map.lanes, map.events),
    [map],
  );

  const arrows = useMemo(() => {
    const at = new Map(placed.map((p) => [p.event.id, p]));
    const laneIndex = new Map(map.lanes.map((lane, i) => [lane.key, i]));
    return placed
      .filter((p) => p.event.target_lane && laneIndex.has(p.event.target_lane))
      .map((p) => {
        const from = at.get(p.event.id)!;
        const toLane = laneIndex.get(p.event.target_lane!)!;
        return {
          id: p.event.id,
          x: from.x + EVENT_WIDTH / 2,
          y1: laneCenterY(from.laneIndex),
          y2: laneCenterY(toLane),
          dashed: p.event.kind === "consulted" || p.event.kind === "blocked",
        };
      })
      .filter((a) => a.y1 !== a.y2);
  }, [placed, map.lanes]);

  return (
    <div className="flex overflow-hidden rounded-md border">
      {/* Sticky gutter: the lane label has to stay put while the timeline scrolls,
          or a wide map becomes a set of anonymous rows. */}
      <div className="shrink-0 border-r bg-muted/30" style={{ width: LABEL_WIDTH }}>
        {map.lanes.map((lane) => (
          <div
            key={lane.key}
            className="flex items-center gap-2 border-b px-3 last:border-b-0"
            style={{ height: LANE_HEIGHT }}
          >
            <span className="text-lg leading-none">{lane.avatar ?? (lane.key === "human" ? "🧑" : "🤖")}</span>
            <div className="min-w-0 flex-1">
              <div className="truncate text-xs font-medium">{lane.label}</div>
              <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
                {lane.status ? (
                  <span className={cn("h-1.5 w-1.5 rounded-full", LANE_TONE[lane.status] ?? "bg-muted")} />
                ) : null}
                <span className="truncate">{lane.status ?? lane.agent_kind}</span>
              </div>
            </div>
          </div>
        ))}
      </div>

      <div className="flex-1 overflow-x-auto">
        <div className="relative" style={{ width, height, minWidth: "100%" }}>
          {map.lanes.map((lane, i) => (
            <div
              key={lane.key}
              className={cn("absolute inset-x-0 border-b", i % 2 === 1 && "bg-muted/20")}
              style={{ top: i * LANE_HEIGHT, height: LANE_HEIGHT }}
            />
          ))}

          <svg className="absolute inset-0 h-full w-full" style={{ width, height }} aria-hidden>
            {arrows.map((a) => (
              <line
                key={a.id}
                x1={a.x}
                y1={a.y1}
                x2={a.x}
                y2={a.y2}
                className="stroke-muted-foreground/50"
                strokeWidth={1.5}
                strokeDasharray={a.dashed ? "4 3" : undefined}
              />
            ))}
          </svg>

          {placed.map(({ event, x, laneIndex }) => {
            const meta = KIND_META[event.kind];
            const { Icon } = meta;
            return (
              <div
                key={event.id}
                className="absolute rounded-md border bg-card px-2 py-1 shadow-sm"
                style={{
                  left: x,
                  top: laneIndex * LANE_HEIGHT + 10,
                  width: EVENT_WIDTH,
                }}
                title={event.detail ?? event.title}
              >
                <div className="flex items-center gap-1.5">
                  <Icon className={cn("h-3.5 w-3.5 shrink-0", meta.tone)} />
                  <span className="truncate text-xs font-medium">{event.title}</span>
                </div>
                <div className="mt-0.5 flex items-center gap-1 text-[10px] text-muted-foreground">
                  <span>{TIME.format(new Date(event.at))}</span>
                  {event.detail ? <span className="truncate">· {event.detail}</span> : null}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
