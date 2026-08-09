import { describe, expect, it } from "vitest";

import type { WorkOrderMapEvent, WorkOrderMapLane } from "@/lib/api/workOrders";
import { EVENT_GAP, EVENT_WIDTH, GUTTER, LANE_HEIGHT, layoutLanes } from "@/lib/workOrderLanes";

const lane = (key: string): WorkOrderMapLane => ({
  key,
  label: key,
  avatar: null,
  agent_kind: "operator",
  status: "done",
});

const event = (
  id: string,
  laneKey: string,
  at: string,
  over: Partial<WorkOrderMapEvent> = {},
): WorkOrderMapEvent => ({
  id,
  lane: laneKey,
  kind: "started",
  at,
  title: id,
  detail: null,
  target_lane: null,
  run_id: null,
  ...over,
});

describe("layoutLanes", () => {
  it("spaces events by how much time passed between them", () => {
    // 30px/min over 10 minutes clears the no-overlap floor, so elapsed time is
    // what sets the distance — a long block shows as a long gap.
    const { placed } = layoutLanes(
      [lane("a")],
      [event("first", "a", "2026-08-09T12:00:00Z"), event("later", "a", "2026-08-09T12:10:00Z")],
      30,
    );

    const x = Object.fromEntries(placed.map((p) => [p.event.id, p.x]));
    expect(x.later - x.first).toBe(300);
  });

  it("never lets time spacing overlap two events", () => {
    // The same ten minutes at a slower scale would collide, so the floor wins and
    // the pair stays readable rather than time-accurate.
    const { placed } = layoutLanes(
      [lane("a")],
      [event("first", "a", "2026-08-09T12:00:00Z"), event("later", "a", "2026-08-09T12:10:00Z")],
      2,
    );

    const x = Object.fromEntries(placed.map((p) => [p.event.id, p.x]));
    expect(x.later - x.first).toBe(EVENT_WIDTH + EVENT_GAP);
  });

  it("keeps a burst readable instead of stacking it", () => {
    // Three events in the same second would otherwise land on identical x.
    const { placed } = layoutLanes(
      [lane("a")],
      [
        event("one", "a", "2026-08-09T12:00:00Z"),
        event("two", "a", "2026-08-09T12:00:00Z"),
        event("three", "a", "2026-08-09T12:00:00Z"),
      ],
    );

    const xs = placed.map((p) => p.x);
    expect(xs).toEqual([GUTTER, GUTTER + EVENT_WIDTH + EVENT_GAP, GUTTER + 2 * (EVENT_WIDTH + EVENT_GAP)]);
  });

  it("lets parallel lanes share the same moment", () => {
    // Two agents working at once must line up vertically — that is the whole
    // point of lanes over a list.
    const { placed } = layoutLanes(
      [lane("a"), lane("b")],
      [event("mine", "a", "2026-08-09T12:05:00Z"), event("theirs", "b", "2026-08-09T12:05:00Z")],
    );

    const [first, second] = placed;
    expect(first.x).toBe(second.x);
    expect(first.laneIndex).not.toBe(second.laneIndex);
  });

  it("orders events by time even when the payload is not sorted", () => {
    const { placed } = layoutLanes(
      [lane("a")],
      [event("late", "a", "2026-08-09T12:09:00Z"), event("early", "a", "2026-08-09T12:00:00Z")],
    );

    expect(placed.map((p) => p.event.id)).toEqual(["early", "late"]);
  });

  it("drops an event whose lane is missing rather than throwing", () => {
    const { placed } = layoutLanes([lane("a")], [event("orphan", "gone", "2026-08-09T12:00:00Z")]);

    expect(placed).toEqual([]);
  });

  it("sizes the canvas to the lanes it was given", () => {
    const { height } = layoutLanes([lane("a"), lane("b")], []);

    expect(height).toBe(2 * LANE_HEIGHT);
  });
});
