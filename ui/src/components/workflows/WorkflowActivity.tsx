"use client";

import { ChevronRight, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Pagination } from "@/components/ui/pagination";
import { Skeleton } from "@/components/ui/skeleton";
import { ACTIVE_STATUSES, StatusPill, duration } from "@/components/workflows/runStatus";
import { getApiErrorMessage } from "@/lib/api/errors";
import { listRecentRuns, type WorkflowRunActivity } from "@/lib/api/workflows";

const POLL_MS = 4000;
const PAGE_SIZE = 8;
// The window of most-recent runs fetched for the feed; paged client-side.
const FEED_LIMIT = 50;

/** Compact relative age of an ISO timestamp, e.g. "2m ago". */
function timeAgo(iso: string): string {
  const s = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

/**
 * Org-wide workflow activity feed: the most-recent runs across every workflow.
 * Each row deep-links into that workflow's Runs page with the run pre-expanded.
 * Polls while any run is still active (running/waiting/retrying/pending).
 */
export function WorkflowActivity() {
  const [runs, setRuns] = useState<WorkflowRunActivity[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reqId = useRef(0);
  const active = useRef(false);
  const stopped = useRef(false);

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setIsLoading(true);
    const id = ++reqId.current;
    try {
      const next = await listRecentRuns(FEED_LIMIT);
      if (id !== reqId.current) return; // superseded by a newer request
      setRuns(next);
      setError(null);
      active.current = next.some((r) => ACTIVE_STATUSES.has(r.status));
    } catch (e: unknown) {
      if (id !== reqId.current) return;
      setError(getApiErrorMessage(e, "Failed to load activity"));
    } finally {
      if (id === reqId.current && !quiet) setIsLoading(false);
    }
    // Keep polling while runs are in flight, even after a failed poll.
    if (id === reqId.current && !stopped.current && active.current) {
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => void load(true), POLL_MS);
    }
  }, []);

  useEffect(() => {
    stopped.current = false;
    void load();
    return () => {
      stopped.current = true;
      if (timer.current) clearTimeout(timer.current);
    };
  }, [load]);

  const pageCount = Math.max(1, Math.ceil(runs.length / PAGE_SIZE));
  const pageRuns = runs.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  // Keep the page in range as polling refreshes the run window.
  useEffect(() => {
    if (page > pageCount) setPage(pageCount);
  }, [page, pageCount]);

  return (
    <Card>
      <CardContent className="space-y-3 pt-6">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold">Recent activity</h2>
            <p className="text-sm text-muted-foreground">
              Latest runs across all workflows{runs.some((r) => ACTIVE_STATUSES.has(r.status)) ? " · live" : ""}.
              Click a run to open its step trace.
            </p>
          </div>
          <Button variant="ghost" size="sm" onClick={() => void load()} aria-label="Refresh activity">
            <RefreshCw className="h-4 w-4" />
            Refresh
          </Button>
        </div>

        {error ? <p className="text-sm text-destructive">{error}</p> : null}

        {isLoading ? (
          <Skeleton className="h-24 w-full" />
        ) : runs.length === 0 ? (
          <p className="rounded-md border bg-card p-6 text-center text-sm text-muted-foreground">
            No workflow runs yet. When a workflow fires (on a record change, form submission, webhook, or a
            manual run), its execution will show up here.
          </p>
        ) : (
          <div className="space-y-2">
            <ul className="max-h-[26rem] divide-y overflow-y-auto rounded-md border">
            {pageRuns.map((run) => (
              <li key={run.id}>
                <Link
                  href={`/workflows/${run.workflow_id}/runs?run=${run.id}`}
                  className="flex items-center gap-3 px-3 py-2 hover:bg-muted/30"
                >
                  <StatusPill status={run.status} />
                  {run.dead_letter ? (
                    <Badge variant="destructive" title="Dead-lettered: needs manual replay">
                      DLQ
                    </Badge>
                  ) : null}
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium">{run.workflow_name}</div>
                    <div className="text-xs text-muted-foreground">
                      {run.trigger_operation} · {timeAgo(run.created_at)}
                      {run.status === "waiting" ? (
                        <span className="ml-1 font-medium text-violet-600 dark:text-violet-400">
                          · action needed
                        </span>
                      ) : null}
                    </div>
                  </div>
                  <span className="hidden text-xs text-muted-foreground sm:inline">
                    {duration(run.started_at, run.finished_at)}
                  </span>
                  <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
                </Link>
              </li>
            ))}
            </ul>
            <Pagination
              page={page}
              pageCount={pageCount}
              total={runs.length}
              onPageChange={setPage}
              itemLabel="run"
            />
          </div>
        )}
      </CardContent>
    </Card>
  );
}
