"use client";

import { Check, X } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { approvalInput } from "@/components/workflows/userTasks";
import { getApiErrorMessage } from "@/lib/api/errors";
import { completeTask } from "@/lib/api/workflows";

/**
 * Approve / reject buttons for a run parked on a human task. Completing the task
 * signals the waiting token with the `{ approved }` decision and advances the run
 * (see {@link completeTask}). Shared by the user-task inbox and the run monitor.
 *
 * A run parked on a non-human wait (timer/message) returns a 409 from the API,
 * which is surfaced inline rather than pre-filtered — the client can't see the
 * wait kind.
 */
export function UserTaskActions({
  runId,
  onCompleted,
}: {
  runId: string;
  /** Called after a successful decision with the run's new status (to refresh). */
  onCompleted?: (status: string) => void;
}) {
  const [busy, setBusy] = useState<null | "approve" | "reject">(null);
  const [error, setError] = useState<string | null>(null);

  const decide = async (approved: boolean) => {
    if (busy) return;
    setBusy(approved ? "approve" : "reject");
    setError(null);
    try {
      const result = await completeTask(runId, approvalInput(approved));
      onCompleted?.(result.status);
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to complete task"));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2">
        <Button size="sm" onClick={() => void decide(true)} disabled={busy !== null}>
          <Check className="h-4 w-4" />
          {busy === "approve" ? "Approving…" : "Approve"}
        </Button>
        <Button
          size="sm"
          variant="outline"
          onClick={() => void decide(false)}
          disabled={busy !== null}
        >
          <X className="h-4 w-4" />
          {busy === "reject" ? "Rejecting…" : "Reject"}
        </Button>
      </div>
      {error ? <p className="text-xs text-destructive">{error}</p> : null}
    </div>
  );
}
