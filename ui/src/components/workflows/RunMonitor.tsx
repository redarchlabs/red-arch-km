"use client";

import { ChevronDown, ChevronRight, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { RunOverlayCanvas } from "@/components/workflows/RunOverlayCanvas";
import { ACTIVE_STATUSES, StatusPill, duration } from "@/components/workflows/runStatus";
import { UserTaskActions } from "@/components/workflows/UserTaskActions";
import { getApiErrorMessage } from "@/lib/api/errors";
import {
  getWorkflow,
  listRunSteps,
  listRuns,
  listVersions,
  type WorkflowDefinition,
  type WorkflowRun,
  type WorkflowRunStep,
} from "@/lib/api/workflows";

const ACTIVE = ACTIVE_STATUSES;
export const POLL_MS = 2500;

export function RunMonitor({
  workflowId,
  initialRunId = null,
}: {
  workflowId: string;
  /** A run to auto-expand + scroll to on first load (from the activity-feed deep link). */
  initialRunId?: string | null;
}) {
  const [runs, setRuns] = useState<WorkflowRun[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [definition, setDefinition] = useState<WorkflowDefinition | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Auto-expand the deep-linked run once, after it first appears in the list.
  const didAutoExpand = useRef(false);
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

  // Expand + scroll to the deep-linked run once it has loaded (the activity feed
  // links here with ?run=<id> so "go into" lands on the run's step trace).
  useEffect(() => {
    if (!initialRunId || didAutoExpand.current) return;
    if (!runs.some((r) => r.id === initialRunId)) return;
    didAutoExpand.current = true;
    setExpanded(initialRunId);
    requestAnimationFrame(() => {
      document
        .querySelector(`[data-run-id="${initialRunId}"]`)
        ?.scrollIntoView({ block: "center", behavior: "smooth" });
    });
  }, [initialRunId, runs]);

  // Load the published graph once so a run row can overlay live state on it.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const wf = await getWorkflow(workflowId);
        if (!wf.active_version_id) return;
        const versions = await listVersions(workflowId);
        const active = versions.find((v) => v.id === wf.active_version_id);
        if (!cancelled && active) setDefinition(active.definition);
      } catch {
        // No overlay if the definition can't be loaded — the step list still works.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [workflowId]);

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
                  onActed={() => void load(true)}
                  definition={definition}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function RunRow({
  run,
  open,
  onToggle,
  onActed,
  definition,
}: {
  run: WorkflowRun;
  open: boolean;
  onToggle: () => void;
  /** Refresh the run list after a human task is completed from this row. */
  onActed: () => void;
  /** The published graph, for the live diagram overlay (null while loading). */
  definition: WorkflowDefinition | null;
}) {
  const [steps, setSteps] = useState<WorkflowRunStep[] | null>(null);
  const [loadingSteps, setLoadingSteps] = useState(false);
  const [showDiagram, setShowDiagram] = useState(false);

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
      <tr
        data-run-id={run.id}
        className="cursor-pointer border-b last:border-0 hover:bg-muted/30 scroll-mt-24"
        onClick={onToggle}
      >
        <td className="px-3 py-2 text-muted-foreground">
          {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        </td>
        <td className="px-3 py-2">
          <StatusPill status={run.status} />
          {run.dead_letter ? (
            <Badge
              variant="destructive"
              className="ml-2 align-middle"
              title="Dead-lettered: retries exhausted with no catcher — needs manual replay"
            >
              DLQ
            </Badge>
          ) : null}
          {run.depth > 0 ? (
            <span className="ml-2 text-xs text-muted-foreground">depth {run.depth}</span>
          ) : null}
          {run.status === "waiting" ? (
            <span className="ml-2 text-xs font-medium text-violet-600 dark:text-violet-400">
              action needed
            </span>
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
            {run.status === "waiting" ? (
              <div className="mb-2 rounded-md border border-violet-500/30 bg-violet-500/10 p-2">
                <p className="mb-1.5 text-xs font-medium text-violet-700 dark:text-violet-300">
                  Parked awaiting a human task — approve or reject to advance the run.
                </p>
                <UserTaskActions runId={run.id} onCompleted={onActed} />
              </div>
            ) : null}
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
            {definition ? (
              <div className="mt-3">
                <Button variant="ghost" size="sm" onClick={() => setShowDiagram((v) => !v)}>
                  {showDiagram ? "Hide diagram" : "Show live diagram"}
                </Button>
                {showDiagram ? (
                  <div className="mt-2 h-80">
                    <RunOverlayCanvas definition={definition} runId={run.id} active={open && showDiagram} />
                  </div>
                ) : null}
              </div>
            ) : null}
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
