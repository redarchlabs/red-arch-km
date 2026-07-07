import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { WorkflowRun } from "@/lib/api/workflows";

// The monitor reaches for these on mount and each poll; mock the API layer.
const listRuns = vi.fn();
const listRunSteps = vi.fn();

vi.mock("@/lib/api/workflows", () => ({
  listRuns: (...args: unknown[]) => listRuns(...args),
  listRunSteps: (...args: unknown[]) => listRunSteps(...args),
}));

import { POLL_MS, RunMonitor } from "./RunMonitor";

function makeRun(status: WorkflowRun["status"]): WorkflowRun {
  return {
    id: `run-${status}`,
    workflow_id: "w1",
    workflow_version_id: "v1",
    trigger_operation: "update",
    record_id: null,
    status,
    conditions_matched: true,
    error: null,
    depth: 0,
    started_at: null,
    finished_at: null,
    created_at: "2026-07-01T00:00:00Z",
  };
}

beforeEach(() => {
  vi.useFakeTimers();
  listRuns.mockReset();
  listRunSteps.mockReset();
  listRunSteps.mockResolvedValue([]);
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("RunMonitor polling", () => {
  it("keeps polling after a failed poll while runs are active (HIGH regression)", async () => {
    const running = makeRun("running");
    listRuns
      .mockResolvedValueOnce([running]) // initial load: active → schedules poll
      .mockRejectedValueOnce(new Error("transient")) // poll 1 fails
      .mockResolvedValueOnce([running]); // poll 2 proves polling did not stop

    await act(async () => {
      render(<RunMonitor workflowId="w1" />);
    });
    expect(listRuns).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_MS);
    });
    expect(listRuns).toHaveBeenCalledTimes(2); // the failing poll ran

    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_MS);
    });
    // The failed poll still scheduled the next one — polling survived the error.
    expect(listRuns).toHaveBeenCalledTimes(3);
  });

  it("stops polling once every run is terminal", async () => {
    listRuns.mockResolvedValue([makeRun("succeeded")]);

    await act(async () => {
      render(<RunMonitor workflowId="w1" />);
    });
    expect(screen.queryByText("succeeded")).not.toBeNull();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_MS * 3);
    });
    // Terminal runs → no further polling scheduled.
    expect(listRuns).toHaveBeenCalledTimes(1);
  });

  it("surfaces an error message when the load fails", async () => {
    listRuns.mockRejectedValue(new Error("boom"));

    await act(async () => {
      render(<RunMonitor workflowId="w1" />);
    });
    expect(screen.queryByText("boom")).not.toBeNull();
  });

  it("renders the empty state when there are no runs", async () => {
    listRuns.mockResolvedValue([]);

    await act(async () => {
      render(<RunMonitor workflowId="w1" />);
    });
    expect(screen.queryByText(/No runs yet/)).not.toBeNull();
  });

  it("flags a dead-lettered run with a DLQ badge", async () => {
    listRuns.mockResolvedValue([{ ...makeRun("failed"), dead_letter: true }]);

    await act(async () => {
      render(<RunMonitor workflowId="w1" />);
    });
    expect(screen.queryByText("DLQ")).not.toBeNull();
  });

  it("shows no DLQ badge for an ordinary failed run", async () => {
    listRuns.mockResolvedValue([makeRun("failed")]);

    await act(async () => {
      render(<RunMonitor workflowId="w1" />);
    });
    expect(screen.queryByText("failed")).not.toBeNull();
    expect(screen.queryByText("DLQ")).toBeNull();
  });
});
