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

import type { WorkOrderEventKind, WorkOrderMap, WorkOrderMapEvent } from "@/lib/api/workOrders";
import { cn } from "@/lib/utils";
import { EVENT_HEIGHT, EVENT_WIDTH, LANE_HEIGHT, eventTopY, layoutLanes } from "@/lib/workOrderLanes";

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
  /** Opening a card shows the run behind it. A card can only carry a title; the
   *  work itself lives in the run's steps. */
  onSelect?: (event: WorkOrderMapEvent) => void;
}

/**
 * One lane per participant, events placed on a shared clock.
 *
 * Cross-lane arrows are drawn in a single SVG behind the pills so a consult
 * visibly leaves one lane and lands in another — which is what distinguishes an
 * agent that is blocked on a peer from one that simply stopped.
 */
export function AgentSwimLanes({ map, onSelect }: AgentSwimLanesProps) {
  const { placed, width, height } = useMemo(
    () => layoutLanes(map.lanes, map.events),
    [map],
  );

  const arrows = useMemo(() => {
    const laneIndex = new Map(map.lanes.map((lane, i) => [lane.key, i]));
    return placed
      .filter((p) => p.event.target_lane && laneIndex.has(p.event.target_lane))
      .map((p) => {
        const toLane = laneIndex.get(p.event.target_lane!)!;
        // Land on the counterpart card in the target lane — the reply to this
        // consult, or the approval this is waiting on. Drawing to the lane's
        // centre instead would run the line straight through whatever card
        // happened to sit at that x, which is what made the map look crossed out.
        const partner = placed.find(
          (q) => q.laneIndex === toLane && Date.parse(q.event.at) >= Date.parse(p.event.at),
        );
        const down = toLane > p.laneIndex;
        return {
          id: p.event.id,
          x1: p.x + EVENT_WIDTH / 2,
          y1: down ? eventTopY(p.laneIndex) + EVENT_HEIGHT : eventTopY(p.laneIndex),
          x2: partner ? partner.x + EVENT_WIDTH / 2 : p.x + EVENT_WIDTH / 2,
          y2: down ? eventTopY(toLane) : eventTopY(toLane) + EVENT_HEIGHT,
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
            <defs>
              <marker id="lane-arrow" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">
                <path d="M0,0 L6,3 L0,6 Z" className="fill-muted-foreground/60" />
              </marker>
            </defs>
            {arrows.map((a) => (
              // An elbow rather than a straight diagonal: the vertical leg leaves
              // the card, the horizontal leg travels in the empty band between
              // lanes, so the line never crosses a card it is not connecting.
              <polyline
                key={a.id}
                points={`${a.x1},${a.y1} ${a.x1},${(a.y1 + a.y2) / 2} ${a.x2},${(a.y1 + a.y2) / 2} ${a.x2},${a.y2}`}
                className="fill-none stroke-muted-foreground/60"
                strokeWidth={1.5}
                strokeDasharray={a.dashed ? "4 3" : undefined}
                markerEnd="url(#lane-arrow)"
              />
            ))}
          </svg>

          {placed.map(({ event, x, laneIndex }) => {
            const meta = KIND_META[event.kind];
            const { Icon } = meta;
            return (
              <button
                type="button"
                key={event.id}
                onClick={onSelect && event.run_id ? () => onSelect(event) : undefined}
                disabled={!onSelect || !event.run_id}
                className="absolute overflow-hidden rounded-md border bg-card px-2 py-1.5 text-left shadow-sm enabled:hover:border-primary enabled:hover:shadow"
                style={{
                  left: x,
                  top: eventTopY(laneIndex),
                  width: EVENT_WIDTH,
                  height: EVENT_HEIGHT,
                }}
                title={event.detail ?? event.title}
              >
                <div className="flex items-center gap-1.5">
                  <Icon className={cn("h-3.5 w-3.5 shrink-0", meta.tone)} />
                  <span className="truncate text-xs font-medium">{event.title}</span>
                  {/* The time never wraps: a second line pushed the card past its
                      lane and made the whole row look like an overflow. */}
                  <span className="ml-auto whitespace-nowrap text-[10px] text-muted-foreground">
                    {TIME.format(new Date(event.at))}
                  </span>
                </div>
                {event.detail ? (
                  <div className="mt-0.5 truncate text-[10px] leading-relaxed text-muted-foreground">
                    {event.detail}
                  </div>
                ) : null}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
