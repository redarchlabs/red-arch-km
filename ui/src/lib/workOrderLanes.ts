/** Geometry for the work-order swim lanes.
 *
 * Kept out of the component, and dependency-free, so the arrangement can be
 * asserted directly rather than inferred from a rendered DOM.
 */

import type { WorkOrderMapEvent, WorkOrderMapLane } from "@/lib/api/workOrders";

export const LANE_HEIGHT = 64;
export const EVENT_WIDTH = 150;
/** Minimum horizontal gap between two events in the same lane. Time alone would
 *  overlap a burst of activity into an unreadable stack, so elapsed time sets the
 *  order and this sets the floor. */
export const EVENT_GAP = 12;
export const GUTTER = 8;

export interface PlacedEvent {
  event: WorkOrderMapEvent;
  x: number;
  laneIndex: number;
}

export interface LaneGeometry {
  placed: PlacedEvent[];
  width: number;
  height: number;
}

/**
 * Place every event on a shared horizontal clock.
 *
 * X is proportional to elapsed time since the first event, so a long block shows
 * as a long gap — the whole reason for a timeline over a list. Events are then
 * pushed right within their own lane until they stop overlapping, which keeps a
 * rapid burst legible without distorting the lanes around it.
 *
 * A run that all happened within a second still reads left-to-right, because the
 * minimum gap applies even when the time span collapses to zero.
 */
export function layoutLanes(
  lanes: WorkOrderMapLane[],
  events: WorkOrderMapEvent[],
  pixelsPerMinute = 26,
): LaneGeometry {
  const laneIndex = new Map(lanes.map((lane, i) => [lane.key, i]));
  const ordered = [...events].sort((a, b) => Date.parse(a.at) - Date.parse(b.at));
  const start = ordered.length > 0 ? Date.parse(ordered[0].at) : 0;

  const rightEdge = new Map<number, number>();
  const placed: PlacedEvent[] = [];
  for (const event of ordered) {
    const index = laneIndex.get(event.lane);
    if (index === undefined) continue; // an event for a lane the payload omitted
    const elapsedMinutes = (Date.parse(event.at) - start) / 60000;
    const wanted = GUTTER + elapsedMinutes * pixelsPerMinute;
    const x = Math.max(wanted, rightEdge.get(index) ?? GUTTER);
    rightEdge.set(index, x + EVENT_WIDTH + EVENT_GAP);
    placed.push({ event, x, laneIndex: index });
  }

  const width = Math.max(...[...rightEdge.values(), 0]) + GUTTER;
  return { placed, width, height: lanes.length * LANE_HEIGHT };
}

/** Vertical centre of a lane, for drawing an event or an arrow into it. */
export function laneCenterY(laneIndex: number): number {
  return laneIndex * LANE_HEIGHT + LANE_HEIGHT / 2;
}
