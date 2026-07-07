/**
 * Pure helpers for the user-task inbox — the actionable list of runs parked on a
 * human task (an approval). A run in the `waiting` state is awaiting an external
 * signal; completing its task calls `POST /workflows/runs/{id}/complete-task`
 * with the decision variables the flow branches on.
 *
 * The backend does not (yet) expose per-run wait tokens to the client, so the
 * inbox treats every `waiting` run as a candidate. `completeTask` returns a 409
 * ("no human task is waiting on this run") for a run parked on a timer/message
 * instead — surfaced to the operator rather than guessed at here.
 */
import type { CompleteTaskInput, WorkflowRun } from "@/lib/api/workflows";

/** The run status that means "parked awaiting an external signal". */
export const WAITING_STATUS = "waiting";

/** The runs the user-task inbox draws from — those currently parked in a wait. */
export function filterWaitingRuns(runs: WorkflowRun[]): WorkflowRun[] {
  return runs.filter((run) => run.status === WAITING_STATUS);
}

/** The decision variables an approve/reject completes a user task with. */
export function approvalVariables(approved: boolean): { approved: boolean } {
  return { approved };
}

/** Build the complete-task payload for an approve (true) / reject (false) decision. */
export function approvalInput(approved: boolean): CompleteTaskInput {
  return { variables: approvalVariables(approved) };
}
