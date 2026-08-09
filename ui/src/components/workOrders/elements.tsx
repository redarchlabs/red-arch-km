"use client";

import { Check, ChevronUp, Loader2, X } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { Markdown } from "@/components/common/Markdown";
import { Button } from "@/components/ui/button";
import { approveApproval, denyApproval, listAgentRunSteps, listApprovals } from "@/lib/api/agents";
import type { AgentRunStep, Approval } from "@/lib/api/agents";
import { getApiErrorMessage } from "@/lib/api/errors";
import {
  getWorkOrder,
  getWorkOrderEntries,
  getWorkOrderMap,
  listWorkOrders,
  setWorkOrderStatus,
  type WorkOrder,
  type WorkOrderEntry,
  type WorkOrderMap,
  type WorkOrderStatus,
} from "@/lib/api/workOrders";

import { AgentSwimLanes } from "./AgentSwimLanes";

/** Re-fetch on a cadence, but only while the tab is visible. A dashboard left
 *  open overnight would otherwise poll thousands of times for a screen nobody is
 *  looking at. */
function usePoll(fn: () => void, ms: number | null | undefined) {
  useEffect(() => {
    if (!ms) return;
    const tick = () => {
      if (document.visibilityState === "visible") fn();
    };
    const timer = setInterval(tick, ms);
    document.addEventListener("visibilitychange", tick);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", tick);
    };
  }, [fn, ms]);
}

const HEIGHTS: Record<string, string> = {
  sm: "h-64",
  md: "h-96",
  lg: "h-[32rem]",
  fill: "h-[70vh]",
};

// ------------------------------------------------------------------ //
// agent_timeline
// ------------------------------------------------------------------ //

interface TimelineProps {
  workOrderId: string | null;
  title?: string | null;
  pollMs?: number | null;
}

export function AgentTimelineNode({ workOrderId, title, pollMs }: TimelineProps) {
  const [map, setMap] = useState<WorkOrderMap | null>(null);
  const [runId, setRunId] = useState<string | null>(null);

  const load = useCallback(() => {
    if (!workOrderId) return;
    void getWorkOrderMap(workOrderId)
      .then(setMap)
      .catch(() => setMap(null));
  }, [workOrderId]);

  useEffect(load, [load]);
  usePoll(load, pollMs);

  if (!workOrderId) return <p className="text-sm text-muted-foreground">No work order selected.</p>;
  if (!map || map.lanes.length === 0) {
    return <p className="text-sm text-muted-foreground">Nothing has run yet.</p>;
  }
  return (
    <div className="space-y-3">
      {title ? <h3 className="text-sm font-medium">{title}</h3> : null}
      <AgentSwimLanes map={map} onSelect={(e) => setRunId(e.run_id)} />
      {runId ? <RunSteps runId={runId} onClose={() => setRunId(null)} /> : null}
    </div>
  );
}

/** What the agent actually did in the run behind a card: the tool calls and their
 *  results. The card can only carry a title; this is the work itself. */
function RunSteps({ runId, onClose }: { runId: string; onClose: () => void }) {
  const [steps, setSteps] = useState<AgentRunStep[] | null>(null);

  useEffect(() => {
    setSteps(null);
    void listAgentRunSteps(runId)
      .then(setSteps)
      .catch(() => setSteps([]));
  }, [runId]);

  return (
    <div className="rounded-md border">
      <div className="flex items-center gap-2 border-b bg-muted/30 px-3 py-2">
        <span className="text-xs font-medium">What this agent did</span>
        <button
          type="button"
          onClick={onClose}
          className="ml-auto text-muted-foreground hover:text-foreground"
          aria-label="Close run detail"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
      <div className="max-h-72 space-y-2 overflow-y-auto p-3">
        {steps === null ? (
          <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
        ) : steps.length === 0 ? (
          <p className="text-xs text-muted-foreground">No recorded steps.</p>
        ) : (
          steps.map((step) => (
            <div key={step.id} className="rounded border bg-card p-2">
              <div className="flex items-center gap-2 text-xs font-medium">
                <span className="text-muted-foreground">{step.kind}</span>
                {step.name ? <span>{step.name}</span> : null}
              </div>
              {step.content ? (
                <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap break-words text-[10px] text-muted-foreground">
                  {JSON.stringify(step.content, null, 2)}
                </pre>
              ) : null}
            </div>
          ))
        )}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ //
// agent_diary
// ------------------------------------------------------------------ //

interface DiaryProps {
  workOrderId: string | null;
  title?: string | null;
  pageSize?: number;
  height?: string;
  pollMs?: number | null;
}

/**
 * The diary, newest at the bottom, loading history as you scroll up.
 *
 * Two things make this read like a conversation rather than a document: it opens
 * pinned to the newest entry, and older pages are fetched only when the reader
 * reaches the top. Scroll position is restored by height delta after a page
 * prepends — without that, adding content above the viewport yanks the reader
 * back to where those entries now sit.
 */
export function AgentDiaryNode({ workOrderId, title, pageSize = 20, height = "md", pollMs }: DiaryProps) {
  const [entries, setEntries] = useState<WorkOrderEntry[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);
  const pinnedToBottom = useRef(true);

  const loadNewest = useCallback(() => {
    if (!workOrderId) return;
    void getWorkOrderEntries(workOrderId, { limit: pageSize })
      .then((page) => {
        setEntries(page.entries);
        setHasMore(page.has_more);
      })
      .catch(() => undefined);
  }, [workOrderId, pageSize]);

  useEffect(loadNewest, [loadNewest]);
  usePoll(loadNewest, pollMs);

  // Stay at the bottom as new entries arrive, but only if the reader is already
  // there — yanking someone out of history they are reading is worse than a
  // missed update.
  useEffect(() => {
    const box = boxRef.current;
    if (box && pinnedToBottom.current) box.scrollTop = box.scrollHeight;
  }, [entries]);

  const loadOlder = useCallback(async () => {
    const box = boxRef.current;
    if (!workOrderId || !hasMore || loading || entries.length === 0 || !box) return;
    setLoading(true);
    const before = box.scrollHeight;
    try {
      const page = await getWorkOrderEntries(workOrderId, { limit: pageSize, before: entries[0].id });
      setEntries((prev) => [...page.entries, ...prev]);
      setHasMore(page.has_more);
      // Restore the reader's place: the content above them just grew.
      requestAnimationFrame(() => {
        box.scrollTop = box.scrollHeight - before;
      });
    } catch {
      // Leave what is already loaded on screen rather than clearing it.
    } finally {
      setLoading(false);
    }
  }, [workOrderId, hasMore, loading, entries, pageSize]);

  const onScroll = () => {
    const box = boxRef.current;
    if (!box) return;
    pinnedToBottom.current = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
    if (box.scrollTop < 60) void loadOlder();
  };

  if (!workOrderId) return <p className="text-sm text-muted-foreground">No work order selected.</p>;

  return (
    <div className="space-y-2">
      {title ? <h3 className="text-sm font-medium">{title}</h3> : null}
      <div
        ref={boxRef}
        onScroll={onScroll}
        className={`space-y-3 overflow-y-auto rounded-md border p-3 ${HEIGHTS[height] ?? HEIGHTS.md}`}
      >
        {hasMore ? (
          <button
            type="button"
            onClick={() => void loadOlder()}
            className="mx-auto flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          >
            {loading ? <Loader2 className="h-3 w-3 animate-spin" /> : <ChevronUp className="h-3 w-3" />}
            Older entries
          </button>
        ) : null}
        {entries.length === 0 ? (
          <p className="text-sm text-muted-foreground">No activity yet.</p>
        ) : (
          entries.map((e) => (
            <div key={e.id} className="rounded-md border bg-muted/20 p-3">
              <div className="mb-1 flex items-center gap-2">
                <span className="text-xs font-medium">{e.role ?? "system"}</span>
                <span className="ml-auto text-[10px] text-muted-foreground">
                  {new Date(e.created_at).toLocaleString()}
                </span>
              </div>
              {/* Agent-authored Markdown. Images are stripped: one emitted via a
                  poisoned document would make the reader's browser fetch an
                  attacker URL. */}
              <Markdown content={e.text} stripImages className="text-sm" />
            </div>
          ))
        )}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ //
// approval_queue
// ------------------------------------------------------------------ //

interface ApprovalQueueProps {
  scope: "work_order" | "org";
  workOrderId: string | null;
  title?: string | null;
  hideWhenEmpty?: boolean;
  pollMs?: number | null;
  /** Run ids belonging to this work order, used to narrow the org-wide list. */
  runIds?: Set<string>;
}

export function ApprovalQueueNode({
  scope,
  title,
  hideWhenEmpty = true,
  pollMs,
  runIds,
}: ApprovalQueueProps) {
  const [items, setItems] = useState<Approval[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    void listApprovals()
      .then((all) => setItems(scope === "org" || !runIds ? all : all.filter((a) => runIds.has(a.run_id))))
      .catch(() => setItems([]));
  }, [scope, runIds]);

  useEffect(load, [load]);
  usePoll(load, pollMs);

  const decide = async (id: string, approve: boolean) => {
    setBusy(id);
    setError(null);
    try {
      await (approve ? approveApproval(id) : denyApproval(id));
      load();
    } catch {
      // 409 means someone else already decided it — a normal race between two
      // open tabs, not a failure worth a stack trace.
      setError("That decision was already made elsewhere.");
      load();
    } finally {
      setBusy(null);
    }
  };

  if (items.length === 0 && hideWhenEmpty) return null;

  return (
    <div className="space-y-2">
      {title ? <h3 className="text-sm font-medium">{title}</h3> : null}
      {error ? <p className="text-xs text-destructive">{error}</p> : null}
      {items.length === 0 ? (
        <p className="text-sm text-muted-foreground">Nothing is waiting on you.</p>
      ) : (
        items.map((a) => (
          <div key={a.id} className="flex items-start gap-3 rounded-md border bg-amber-50/50 p-3 dark:bg-amber-950/20">
            <div className="min-w-0 flex-1">
              <div className="text-sm font-medium">{a.tool_name}</div>
              <pre className="mt-0.5 max-h-20 overflow-auto whitespace-pre-wrap break-words text-[10px] text-muted-foreground">
                {JSON.stringify(a.arguments, null, 2)}
              </pre>
            </div>
            <div className="flex shrink-0 gap-1">
              <Button size="sm" disabled={busy === a.id} onClick={() => void decide(a.id, true)}>
                <Check className="mr-1 h-3 w-3" /> Approve
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={busy === a.id}
                onClick={() => void decide(a.id, false)}
              >
                <X className="mr-1 h-3 w-3" /> Deny
              </Button>
            </div>
          </div>
        ))
      )}
    </div>
  );
}

// ------------------------------------------------------------------ //
// work_order_actions
// ------------------------------------------------------------------ //

interface ActionsProps {
  workOrderId: string | null;
  title?: string | null;
  showSummary?: boolean;
}

/** How each transition reads to the person clicking it. "in_progress" is the one
 *  that matters: on an assigned order it dispatches the agent, so it is labelled
 *  as starting work rather than as setting a status. */
const ACTION_LABELS: Record<string, string> = {
  awaiting_approval: "Send for approval",
  approved: "Approve",
  in_progress: "Start work",
  done: "Mark done",
  cancelled: "Cancel",
  draft: "Back to draft",
};

export function WorkOrderActionsNode({ workOrderId, title, showSummary = true }: ActionsProps) {
  const [wo, setWo] = useState<WorkOrder | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    if (!workOrderId) return;
    void getWorkOrder(workOrderId)
      .then(setWo)
      .catch(() => setWo(null));
  }, [workOrderId]);

  useEffect(load, [load]);

  const move = async (status: string) => {
    if (!workOrderId) return;
    setBusy(true);
    setError(null);
    try {
      setWo(await setWorkOrderStatus(workOrderId, status as WorkOrderStatus));
    } catch (e: unknown) {
      // Starting an order assigned to a disabled agent is refused here, and the
      // server's message says which agent — worth showing verbatim rather than
      // flattening to "failed".
      setError(getApiErrorMessage(e, "Could not change the status"));
    } finally {
      setBusy(false);
    }
  };

  if (!workOrderId) return <p className="text-sm text-muted-foreground">No work order selected.</p>;
  if (!wo) return null;

  return (
    <div className="space-y-2">
      {title ? <h3 className="text-sm font-medium">{title}</h3> : null}
      {showSummary ? (
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium">{wo.title}</span>
          <span className="rounded border px-1.5 py-0.5 text-[10px] text-muted-foreground">{wo.status}</span>
        </div>
      ) : null}
      {error ? <p className="text-xs text-destructive">{error}</p> : null}
      <div className="flex flex-wrap gap-2">
        {(wo.allowed_transitions ?? []).map((s) => (
          <Button key={s} size="sm" variant="outline" disabled={busy} onClick={() => void move(s)}>
            {ACTION_LABELS[s] ?? s}
          </Button>
        ))}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ //
// work_order_list
// ------------------------------------------------------------------ //

interface WorkOrderListProps {
  title?: string | null;
  statuses?: string[];
  detailViewId?: string | null;
  limit?: number;
  pollMs?: number | null;
}

export function WorkOrderListNode({ title, statuses, detailViewId, limit = 25, pollMs }: WorkOrderListProps) {
  const [orders, setOrders] = useState<WorkOrder[]>([]);

  const load = useCallback(() => {
    void listWorkOrders()
      .then((all) => {
        const filtered = statuses?.length ? all.filter((w) => statuses.includes(w.status)) : all;
        setOrders(filtered.slice(0, limit));
      })
      .catch(() => setOrders([]));
  }, [statuses, limit]);

  useEffect(load, [load]);
  usePoll(load, pollMs);

  return (
    <div className="space-y-2">
      {title ? <h3 className="text-sm font-medium">{title}</h3> : null}
      {orders.length === 0 ? (
        <p className="text-sm text-muted-foreground">No work orders.</p>
      ) : (
        <div className="divide-y rounded-md border">
          {orders.map((wo) => {
            const row = (
              <div className="flex items-center gap-3 px-3 py-2">
                <span className="min-w-0 flex-1 truncate text-sm">{wo.title}</span>
                <span className="shrink-0 rounded border px-1.5 py-0.5 text-[10px] text-muted-foreground">
                  {wo.status}
                </span>
              </div>
            );
            // Inert rows without a target: a link to nowhere is worse than text.
            return detailViewId ? (
              <Link
                key={wo.id}
                href={`/views/${detailViewId}/view?record_id=${encodeURIComponent(wo.id)}`}
                className="block hover:bg-muted/40"
              >
                {row}
              </Link>
            ) : (
              <div key={wo.id}>{row}</div>
            );
          })}
        </div>
      )}
    </div>
  );
}
