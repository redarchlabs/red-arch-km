"use client";

import { RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { fetchCeleryStatus, type BeatStatus, type CeleryStatus } from "@/lib/api/celery";
import { getApiErrorMessage } from "@/lib/api/errors";

const BEAT_BADGE: Record<BeatStatus["status"], { variant: "default" | "secondary" | "destructive"; label: string }> = {
  ok: { variant: "default", label: "running" },
  stale: { variant: "secondary", label: "stale" },
  down: { variant: "destructive", label: "down" },
};

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

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      setData(await fetchCeleryStatus());
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to load Celery status"));
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const beat = data?.beat;
  const beatBadge = beat ? BEAT_BADGE[beat.status] : null;

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
                <div className="overflow-x-auto rounded-md border">
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
              ) : (
                <p className="text-xs text-muted-foreground">
                  No schedule published — beat has not reported in yet.
                </p>
              )}
            </>
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
            <div className="overflow-x-auto rounded-md border">
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
          ) : data ? (
            <p className="rounded-md border border-dashed p-3 text-sm text-muted-foreground">
              Queue is empty — workers are keeping up (or no work has been enqueued).
            </p>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}
