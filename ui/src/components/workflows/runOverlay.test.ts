import { describe, expect, it } from "vitest";

import { chromeFromNodeStatuses, mapRunStatus, nodeStatusesFromSteps } from "./runOverlay";

describe("mapRunStatus", () => {
  it("maps backend statuses to the ring vocabulary", () => {
    expect(mapRunStatus("succeeded")).toBe("completed");
    expect(mapRunStatus("skipped")).toBe("completed");
    expect(mapRunStatus("failed")).toBe("failed");
    expect(mapRunStatus("running")).toBe("active");
    expect(mapRunStatus("waiting")).toBe("waiting");
    expect(mapRunStatus("retrying")).toBe("waiting");
  });

  it("falls back to idle for unknown/missing", () => {
    expect(mapRunStatus(undefined)).toBe("idle");
    expect(mapRunStatus("pending")).toBe("idle");
  });
});

describe("chromeFromNodeStatuses", () => {
  it("builds a chrome entry per node", () => {
    const chrome = chromeFromNodeStatuses({ a: "succeeded", b: "waiting", c: "failed" });
    expect(chrome).toEqual({
      a: { status: "completed" },
      b: { status: "waiting" },
      c: { status: "failed" },
    });
  });

  it("tolerates an empty/absent map", () => {
    expect(chromeFromNodeStatuses({})).toEqual({});
    expect(chromeFromNodeStatuses(undefined as unknown as Record<string, string>)).toEqual({});
  });
});

describe("nodeStatusesFromSteps", () => {
  it("step status wins; a parked token colors a node with no step", () => {
    const nodes = nodeStatusesFromSteps(
      [{ node_id: "log1", status: "succeeded" }],
      [
        { node_id: "log1", status: "dead" }, // ignored — step already recorded
        { node_id: "approve", status: "waiting" },
        { node_id: "worker", status: "running" },
      ],
    );
    expect(nodes).toEqual({ log1: "succeeded", approve: "waiting", worker: "running" });
  });

  it("returns empty when there is nothing to show", () => {
    expect(nodeStatusesFromSteps([], [])).toEqual({});
  });
});
