import { describe, expect, it } from "vitest";

import type { WorkflowRun } from "@/lib/api/workflows";
import { approvalInput, approvalVariables, filterWaitingRuns, WAITING_STATUS } from "./userTasks";

function run(status: WorkflowRun["status"], id: string = status): WorkflowRun {
  return {
    id,
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

describe("filterWaitingRuns", () => {
  it("keeps only runs parked in the waiting state", () => {
    const runs = [run("waiting", "a"), run("running", "b"), run("waiting", "c"), run("succeeded", "d")];
    expect(filterWaitingRuns(runs).map((r) => r.id)).toEqual(["a", "c"]);
  });

  it("returns an empty list when nothing is waiting", () => {
    expect(filterWaitingRuns([run("succeeded"), run("failed")])).toEqual([]);
  });

  it("uses the WAITING_STATUS constant", () => {
    expect(WAITING_STATUS).toBe("waiting");
  });
});

describe("approval decision helpers", () => {
  it("maps approve/reject to the {approved} decision variable", () => {
    expect(approvalVariables(true)).toEqual({ approved: true });
    expect(approvalVariables(false)).toEqual({ approved: false });
  });

  it("wraps the decision in a complete-task variables payload", () => {
    expect(approvalInput(true)).toEqual({ variables: { approved: true } });
    expect(approvalInput(false)).toEqual({ variables: { approved: false } });
  });
});
