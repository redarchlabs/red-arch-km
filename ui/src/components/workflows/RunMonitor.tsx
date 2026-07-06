"use client";

import { ChevronDown, ChevronRight, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { getApiErrorMessage } from "@/lib/api/errors";
import {
  listRunSteps,
  listRuns,
  type WorkflowRun,
  type WorkflowRunStep,
} from "@/lib/api/workflows";

const STATUS_CLASSES: Record<string, string> = {
  succeeded: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
  failed: "bg-rose-500/15 text-rose-600 dark:text-rose-400",
  running: "bg-sky-500/15 text-sky-600 dark:text-sky-400",
  waiting: "bg-violet-500/15 text-violet-600 dark:text-violet-400",
  retrying: "bg-amber-500/15 text-amber-600 dark:text-amber-400",
  skipped: "bg-amber-500/15 text-amber-600 dark:text-amber-400",
  pending: "bg-muted text-muted-foreground",
};

function StatusPill({ status }: { status: string }) {
  return (
    <span className={cn("rounded-full px-2 py-0.5 text-xs font-medium", STATUS_CLASSES[status] ?? "bg-muted")}>
      {status}
    </span>
  );
}

function duration(start: string | null, end: string | null): string {
  if (!start || !end) return "—";
  const ms = new Date(end).getTime() - new Date(start).getTime();
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

const ACTIVE = new Set(["pending", "running", "retrying", "waiting"]);
export const POLL_MS = 2500;

export function RunMonitor({ workflowId }: { workflowId: string }) {
  const [runs, setRuns] = useState<WorkflowRun[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Monotonic request id: a slow earlier response with a stale id is ignored so
  // it can't overwrite the result of a newer request.
  const reqId = useRef(0);
  // Remember whether the last successful poll saw active runs, so a *failed*
  // poll can still decide to keep polling instead of stopping forever.
  const active = useRef(false);
  const stopped = useRef(false);

  const load = useCallback(
    async (quiet = false) => {
      if (!quiet) setIsLoading(true);
      const id = ++reqId.current;
      try {
        const next = await listRuns(workflowId);
        if (id !== reqId.current) return; // superseded by a newer request
        setRuns(next);
        setError(null);
        active.current = next.some((r) => ACTIVE.has(r.status));
      } catch (e: unknown) {
        if (id !== reqId.current) return;
        setError(getApiErrorMessage(e, "Failed to load runs"));
        // Leave `active` at its last-known value: a transient failure must not
        // permanently halt polling while runs are still in flight.
      } finally {
        if (id === reqId.current && !quiet) setIsLoading(false);
      }
      // Schedule the next poll unconditionally (even after a failed poll) while
      // runs are active — not off a [runs] effect that a no-op update wouldn't
      // retrigger. Only the freshest request reaches here (stale ones returned).
      if (id === reqId.current && !stopped.current && active.current) {
        if (timer.current) clearTimeout(timer.current);
        timer.current = setTimeout(() => void load(true), POLL_MS);
      }
    },
    [workflowId],
  );

  useEffect(() => {
    stopped.current = false;
    void load();
    return () => {
      stopped.current = true;
      if (timer.current) clearTimeout(timer.current);
    };
  }, [load]);

  if (isLoading) return <Skeleton className="h-40 w-full" />;

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          {runs.length} recent run{runs.length === 1 ? "" : "s"}
          {runs.some((r) => ACTIVE.has(r.status)) ? " · live" : ""}
        </p>
        <Button variant="ghost" size="sm" onClick={() => void load()}>
          <RefreshCw className="h-4 w-4" />
          Refresh
        </Button>
      </div>

      {error ? <p className="text-sm text-destructive">{error}</p> : null}

      {runs.length === 0 ? (
        <p className="rounded-md border bg-card p-6 text-center text-sm text-muted-foreground">
          No runs yet. When a record on this entity changes and the workflow is enabled + published,
          runs will appear here.
        </p>
      ) : (
        <div className="overflow-hidden rounded-md border">
          <table className="w-full text-sm">
            <thead className="border-b bg-muted/50 text-left">
              <tr>
                <th className="w-8 px-3 py-2" />
                <th className="px-3 py-2 font-medium">Status</th>
                <th className="px-3 py-2 font-medium">Trigger</th>
                <th className="px-3 py-2 font-medium">Started</th>
                <th className="px-3 py-2 font-medium">Duration</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <RunRow
                  key={run.id}
                  run={run}
                  open={expanded === run.id}
                  onToggle={() => setExpanded(expanded === run.id ? null : run.id)}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function RunRow({ run, open, onToggle }: { run: WorkflowRun; open: boolean; onToggle: () => void }) {
  const [steps, setSteps] = useState<WorkflowRunStep[] | null>(null);
  const [loadingSteps, setLoadingSteps] = useState(false);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoadingSteps(true);
    listRunSteps(run.id)
      .then((s) => !cancelled && setSteps(s))
      .catch(() => !cancelled && setSteps([]))
      .finally(() => !cancelled && setLoadingSteps(false));
    return () => {
      cancelled = true;
    };
  }, [open, run.id, run.status]);

  return (
    <>
      <tr className="cursor-pointer border-b last:border-0 hover:bg-muted/30" onClick={onToggle}>
        <td className="px-3 py-2 text-muted-foreground">
          {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        </td>
        <td className="px-3 py-2">
          <StatusPill status={run.status} />
          {run.depth > 0 ? (
            <span className="ml-2 text-xs text-muted-foreground">depth {run.depth}</span>
          ) : null}
        </td>
        <td className="px-3 py-2">{run.trigger_operation}</td>
        <td className="px-3 py-2 text-muted-foreground">
          {run.started_at ? new Date(run.started_at).toLocaleString() : "—"}
        </td>
        <td className="px-3 py-2 text-muted-foreground">{duration(run.started_at, run.finished_at)}</td>
      </tr>
      {open ? (
        <tr className="border-b last:border-0 bg-muted/20">
          <td />
          <td colSpan={4} className="px-3 py-2">
            {run.error ? <p className="mb-2 text-xs text-destructive">Error: {run.error}</p> : null}
            {loadingSteps ? (
              <Skeleton className="h-10 w-full" />
            ) : steps && steps.length > 0 ? (
              <div className="space-y-1">
                {steps.map((step) => (
                  <StepRow key={step.id} step={step} />
                ))}
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">
                No action steps{run.conditions_matched ? "" : " (conditions did not match)"}.
              </p>
            )}
          </td>
        </tr>
      ) : null}
    </>
  );
}

function StepRow({ step }: { step: WorkflowRunStep }) {
  return (
    <div className="rounded-md border bg-background p-2 text-xs">
      <div className="flex items-center gap-2">
        <StatusPill status={step.status} />
        <span className="font-medium">{step.action_type}</span>
        {step.attempts > 1 || step.status === "retrying" ? (
          <Badge variant="outline">
            attempt {step.attempts}/{step.max_attempts}
          </Badge>
        ) : null}
        {step.next_retry_at ? (
          <span className="text-muted-foreground">
            retry at {new Date(step.next_retry_at).toLocaleTimeString()}
          </span>
        ) : null}
        <span className="ml-auto text-muted-foreground">{duration(step.started_at, step.finished_at)}</span>
      </div>
      {step.error ? <p className="mt-1 text-destructive">{step.error}</p> : null}
      {step.output ? (
        <pre className="mt-1 overflow-x-auto text-[11px] text-muted-foreground">
          {JSON.stringify(step.output, null, 2)}
        </pre>
      ) : null}
    </div>
  );
}
