"use client";

import { ArrowLeft, Inbox as InboxIcon, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { UserTaskActions } from "@/components/workflows/UserTaskActions";
import { filterWaitingRuns } from "@/components/workflows/userTasks";
import { getApiErrorMessage } from "@/lib/api/errors";
import { listRuns, listWorkflows, type WorkflowRun } from "@/lib/api/workflows";

interface InboxItem extends WorkflowRun {
  workflowName: string;
}

export default function InboxPage() {
  const [items, setItems] = useState<InboxItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const workflows = await listWorkflows();
      const nameById = new Map(workflows.map((w) => [w.id, w.name]));
      // One run query per workflow; a single failure shouldn't blank the inbox.
      const runLists = await Promise.all(workflows.map((w) => listRuns(w.id).catch(() => [])));
      const waiting = filterWaitingRuns(runLists.flat());
      waiting.sort((a, b) => (a.created_at < b.created_at ? 1 : -1)); // newest first
      setItems(waiting.map((run) => ({ ...run, workflowName: nameById.get(run.workflow_id) ?? "—" })));
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to load the inbox"));
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Link href="/workflows" className="text-muted-foreground hover:text-foreground">
          <ArrowLeft className="h-5 w-5" />
        </Link>
        <div className="flex-1">
          <h1 className="text-2xl font-semibold">Task inbox</h1>
          <p className="text-sm text-muted-foreground">
            Workflow runs parked on a human task, waiting for an approval decision.
          </p>
        </div>
        <Button variant="ghost" size="sm" onClick={() => void load()}>
          <RefreshCw className="h-4 w-4" />
          Refresh
        </Button>
      </div>

      {error ? <p className="text-sm text-destructive">{error}</p> : null}

      {isLoading ? (
        <Skeleton className="h-40 w-full" />
      ) : items.length === 0 ? (
        <div className="flex flex-col items-center gap-2 rounded-md border bg-card p-10 text-center">
          <InboxIcon className="h-8 w-8 text-muted-foreground" />
          <p className="text-sm text-muted-foreground">
            Nothing waiting. Runs parked on a human task will appear here to approve or reject.
          </p>
        </div>
      ) : (
        <ul className="space-y-2">
          {items.map((item) => (
            <li key={item.id} className="flex flex-wrap items-center gap-3 rounded-md border bg-card p-3">
              <InboxIcon className="h-4 w-4 text-muted-foreground" />
              <div className="min-w-0 flex-1">
                <Link href={`/workflows/${item.workflow_id}/runs`} className="text-sm font-medium hover:underline">
                  {item.workflowName}
                </Link>
                <div className="text-xs text-muted-foreground">
                  {item.trigger_operation}
                  {item.started_at ? ` · started ${new Date(item.started_at).toLocaleString()}` : ""}
                </div>
              </div>
              <UserTaskActions runId={item.id} onCompleted={() => void load()} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
