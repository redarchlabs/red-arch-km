"use client";

import { Ban, FileText, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import {
  cancelJob,
  fetchCeleryStatus,
  fetchJobLogs,
  type BeatStatus,
  type CeleryActiveTask,
  type CeleryStatus,
  type JobLogEntry,
} from "@/lib/api/celery";
import { getApiErrorMessage } from "@/lib/api/errors";

const BEAT_BADGE: Record<BeatStatus["status"], { variant: "default" | "secondary" | "destructive"; label: string }> = {
  ok: { variant: "default", label: "running" },
  stale: { variant: "secondary", label: "stale" },
  down: { variant: "destructive", label: "down" },
};

// Auto-refresh cadence for the console — frequent enough to watch an ingest move.
const REFRESH_MS = 5000;

function formatInterval(seconds: number | null): string {
  if (seconds == null) return "—";
  if (seconds < 60) return `every ${Math.round(seconds)}s`;
  if (seconds < 3600) return `every ${Math.round(seconds / 60)}m`;
  if (seconds < 86400) return `every ${Math.round(seconds / 3600)}h`;
  return `every ${Math.round(seconds / 86400)}d`;
}

export function CeleryMonitor() {
  const [data, setData] = useState<CeleryStatus | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [logsFor, setLogsFor] = useState<string | null>(null);

  // `silent` refreshes (auto-poll) skip the skeleton so the tables don't flicker.
  const load = useCallback(async (silent = false) => {
    if (!silent) setIsLoading(true);
    setError(null);
    try {
      setData(await fetchCeleryStatus());
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to load Celery status"));
    } finally {
      if (!silent) setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Auto-refresh so an operator can watch the queue drain / a task run live.
  useEffect(() => {
    const timer = setInterval(() => void load(true), REFRESH_MS);
    return () => clearInterval(timer);
  }, [load]);

  const handleCancel = useCallback(
    async (task: CeleryActiveTask) => {
      if (!task.document_id) return;
      if (!window.confirm("Cancel this ingest? Any partial index for it is discarded.")) return;
      try {
        await cancelJob(task.document_id);
        await load(true);
      } catch (e: unknown) {
        window.alert(getApiErrorMessage(e, "Cancel failed"));
      }
    },
    [load],
  );

  const beat = data?.beat;
  const beatBadge = beat ? BEAT_BADGE[beat.status] : null;
  const active = data?.active ?? [];

  return (
    <div className="space-y-4">
      {/* Beat status + schedule */}
      <Card>
        <CardContent className="space-y-4 pt-6">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold">Celery beat (scheduler)</h2>
              <p className="text-sm text-muted-foreground">
                Fires the periodic jobs that drain the workflow outbox and maintain partitions.
              </p>
            </div>
            <Button variant="outline" size="sm" onClick={() => void load()} disabled={isLoading}>
              <RefreshCw className="h-4 w-4" />
              Refresh
            </Button>
          </div>

          {error ? <p className="text-sm text-destructive">{error}</p> : null}

          {isLoading ? (
            <Skeleton className="h-24 w-full" />
          ) : data && beat && beatBadge ? (
            <>
              <div className="rounded-md border p-3">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium">Scheduler</span>
                  <Badge variant={beatBadge.variant}>{beatBadge.label}</Badge>
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                  {beat.detail
                    ? beat.detail
                    : beat.last_tick
                      ? `Last tick ${beat.age_seconds ?? 0}s ago`
                      : "No heartbeat recorded yet."}
                </p>
              </div>

              {data.schedule.length > 0 ? (
                <>
                  <div className="hidden overflow-x-auto rounded-md border md:block">
                    <table className="w-full text-sm">
                      <thead className="bg-muted/50 text-left text-xs text-muted-foreground">
                        <tr>
                          <th className="px-3 py-2 font-medium">Schedule</th>
                          <th className="px-3 py-2 font-medium">Task</th>
                          <th className="px-3 py-2 font-medium">Cadence</th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.schedule.map((entry) => (
                          <tr key={entry.name} className="border-t">
                            <td className="px-3 py-2 font-medium">{entry.name}</td>
                            <td className="px-3 py-2 font-mono text-xs text-muted-foreground">{entry.task ?? "—"}</td>
                            <td className="px-3 py-2 whitespace-nowrap">{formatInterval(entry.schedule_seconds)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <ul className="space-y-2 md:hidden">
                    {data.schedule.map((entry) => (
                      <li key={entry.name} className="rounded-md border p-3 text-sm">
                        <p className="font-medium">{entry.name}</p>
                        <p className="mt-1 font-mono text-xs break-all text-muted-foreground">
                          {entry.task ?? "—"}
                        </p>
                        <p className="mt-1 text-xs">{formatInterval(entry.schedule_seconds)}</p>
                      </li>
                    ))}
                  </ul>
                </>
              ) : (
                <p className="text-xs text-muted-foreground">
                  No schedule published — beat has not reported in yet.
                </p>
              )}
            </>
          ) : null}
        </CardContent>
      </Card>

      {/* Active (running) tasks */}
      <Card>
        <CardContent className="space-y-3 pt-6">
          <div>
            <h2 className="text-lg font-semibold">Running now</h2>
            <p className="text-sm text-muted-foreground">
              {data ? (
                <>
                  <span className="font-medium text-foreground">{active.length}</span> task
                  {active.length === 1 ? "" : "s"} executing on workers
                </>
              ) : (
                "Tasks currently executing on a worker."
              )}
            </p>
          </div>

          {isLoading ? (
            <Skeleton className="h-16 w-full" />
          ) : active.length > 0 ? (
            <div className="overflow-x-auto rounded-md border">
              <table className="w-full text-sm">
                <thead className="bg-muted/50 text-left text-xs text-muted-foreground">
                  <tr>
                    <th className="px-3 py-2 font-medium">Task</th>
                    <th className="px-3 py-2 font-medium">ID</th>
                    <th className="px-3 py-2 font-medium">Worker</th>
                    <th className="px-3 py-2 font-medium">Progress</th>
                    <th className="px-3 py-2 text-right font-medium">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {active.map((task, i) => (
                    <tr key={task.id ?? i} className="border-t align-top">
                      <td className="px-3 py-2 font-mono text-xs">{task.task ?? "(unknown)"}</td>
                      <td className="px-3 py-2 font-mono text-xs text-muted-foreground">
                        {task.id ? task.id.slice(0, 8) : "—"}
                      </td>
                      <td className="px-3 py-2 font-mono text-xs text-muted-foreground">{task.worker ?? "—"}</td>
                      <td className="px-3 py-2">
                        {task.percent != null ? (
                          <div className="min-w-28">
                            <div className="flex items-center justify-between text-xs text-muted-foreground">
                              <span>{task.stage ?? "—"}</span>
                              <span>{task.percent}%</span>
                            </div>
                            <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-muted">
                              <div
                                className="h-full rounded-full bg-primary transition-all"
                                style={{ width: `${task.percent}%` }}
                              />
                            </div>
                          </div>
                        ) : (
                          <span className="text-xs text-muted-foreground">—</span>
                        )}
                      </td>
                      <td className="px-3 py-2">
                        <div className="flex items-center justify-end gap-1">
                          {task.document_id ? (
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => setLogsFor(task.document_id)}
                              className="h-7 gap-1 px-2 text-xs"
                            >
                              <FileText className="h-3.5 w-3.5" />
                              Logs
                            </Button>
                          ) : null}
                          {task.document_id ? (
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => void handleCancel(task)}
                              className="h-7 gap-1 px-2 text-xs text-destructive hover:text-destructive"
                            >
                              <Ban className="h-3.5 w-3.5" />
                              Cancel
                            </Button>
                          ) : null}
                          {!task.document_id ? <span className="text-xs text-muted-foreground">—</span> : null}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : data ? (
            <p className="rounded-md border border-dashed p-3 text-sm text-muted-foreground">
              Nothing running right now.
            </p>
          ) : null}
        </CardContent>
      </Card>

      {/* Queue peek */}
      <Card>
        <CardContent className="space-y-3 pt-6">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold">Queue</h2>
              <p className="text-sm text-muted-foreground">
                {data ? (
                  <>
                    <span className="font-medium text-foreground">{data.depth}</span> pending in{" "}
                    <span className="font-mono text-xs">{data.queue_name}</span>
                    {data.truncated ? ` (showing first ${data.items.length})` : ""}
                  </>
                ) : (
                  "Pending messages waiting for a worker."
                )}
              </p>
            </div>
          </div>

          {isLoading ? (
            <Skeleton className="h-24 w-full" />
          ) : data && data.items.length > 0 ? (
            <>
              <div className="hidden overflow-x-auto rounded-md border md:block">
                <table className="w-full text-sm">
                  <thead className="bg-muted/50 text-left text-xs text-muted-foreground">
                    <tr>
                      <th className="px-3 py-2 font-medium">Task</th>
                      <th className="px-3 py-2 font-medium">ID</th>
                      <th className="px-3 py-2 font-medium">Args</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.items.map((item, i) => (
                      <tr key={item.id ?? i} className="border-t align-top">
                        <td className="px-3 py-2 font-mono text-xs">{item.task ?? "(unparseable)"}</td>
                        <td className="px-3 py-2 font-mono text-xs text-muted-foreground">
                          {item.id ? item.id.slice(0, 8) : "—"}
                        </td>
                        <td className="px-3 py-2 font-mono text-xs text-muted-foreground">
                          {[item.args, item.kwargs].filter(Boolean).join(" ") || "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <ul className="space-y-2 md:hidden">
                {data.items.map((item, i) => (
                  <li key={item.id ?? i} className="rounded-md border p-3 text-xs">
                    <p className="font-mono">{item.task ?? "(unparseable)"}</p>
                    <p className="mt-1 font-mono text-muted-foreground">
                      ID: {item.id ? item.id.slice(0, 8) : "—"}
                    </p>
                    <p className="mt-1 font-mono break-all text-muted-foreground">
                      Args: {[item.args, item.kwargs].filter(Boolean).join(" ") || "—"}
                    </p>
                  </li>
                ))}
              </ul>
            </>
          ) : data ? (
            <p className="rounded-md border border-dashed p-3 text-sm text-muted-foreground">
              Queue is empty — workers are keeping up (or no work has been enqueued).
            </p>
          ) : null}
        </CardContent>
      </Card>

      {logsFor ? <JobLogsDialog documentId={logsFor} onClose={() => setLogsFor(null)} /> : null}
    </div>
  );
}

function logLevelClass(level: string | null): string {
  if (level === "error") return "text-destructive";
  if (level === "warning") return "text-amber-600 dark:text-amber-500";
  return "text-foreground";
}

interface JobLogsDialogProps {
  documentId: string;
  onClose: () => void;
}

/** Modal showing an ingest job's log lines, fetched by document id. */
function JobLogsDialog({ documentId, onClose }: JobLogsDialogProps) {
  const [events, setEvents] = useState<JobLogEntry[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    fetchJobLogs(documentId)
      .then((res) => {
        if (!cancelled) setEvents(res.events);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(getApiErrorMessage(e, "Failed to load job logs"));
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [documentId]);

  return (
    <Dialog open onClose={onClose} className="max-w-2xl">
      <DialogHeader>
        <DialogTitle>Ingest log</DialogTitle>
        <p className="font-mono text-xs text-muted-foreground">{documentId}</p>
      </DialogHeader>
      {isLoading ? (
        <Skeleton className="h-32 w-full" />
      ) : error ? (
        <p className="text-sm text-destructive">{error}</p>
      ) : events.length === 0 ? (
        <p className="text-sm text-muted-foreground">No log lines for this job (may have expired).</p>
      ) : (
        <ol className="max-h-96 space-y-1 overflow-y-auto font-mono text-xs">
          {events.map((e, i) => (
            <li key={`${e.ts ?? ""}-${i}`} className="flex gap-2">
              <span className="shrink-0 text-muted-foreground">
                {e.ts ? new Date(e.ts).toLocaleTimeString() : ""}
              </span>
              <span className={logLevelClass(e.level)}>{e.message}</span>
            </li>
          ))}
        </ol>
      )}
    </Dialog>
  );
}
