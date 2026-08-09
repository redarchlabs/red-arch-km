"use client";

import { Check, ChevronUp, Loader2, X } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { Markdown } from "@/components/common/Markdown";
import { Button } from "@/components/ui/button";
import {
  answerQuestion,
  approveApproval,
  declineQuestion,
  denyApproval,
  listAgentRunSteps,
  listAgents,
  listApprovals,
  listQuestions,
} from "@/lib/api/agents";
import type { Agent, AgentQuestion, AgentRunStep, Approval } from "@/lib/api/agents";
import { getApiErrorMessage } from "@/lib/api/errors";
import {
  assignWorkOrder,
  createWorkOrder,
  getWorkOrder,
  getWorkOrderEntries,
  getWorkOrderMap,
  listWorkOrders,
  replyToWorkOrder,
  setWorkOrderMode,
  setWorkOrderStatus,
  type WorkOrder,
  type WorkOrderDetail,
  type WorkOrderEntry,
  type WorkOrderMode,
  type WorkOrderMap,
  type WorkOrderStatus,
} from "@/lib/api/workOrders";

import { cn } from "@/lib/utils";

import { AgentSwimLanes } from "./AgentSwimLanes";
import { readableStep } from "./runSteps";

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
          steps.map((step) => {
            const readable = readableStep(step);
            return (
              <div
                key={step.id}
                className={cn("rounded border bg-card p-2", readable.failed && "border-destructive/50")}
              >
                <div className="text-xs font-medium">{readable.title}</div>
                {readable.facts.length > 0 ? (
                  <dl className="mt-1 space-y-0.5">
                    {readable.facts.map((fact) => (
                      <div key={fact.label} className="flex gap-2 text-xs">
                        <dt className="shrink-0 text-muted-foreground">{fact.label}</dt>
                        <dd className="min-w-0 flex-1 break-words">{fact.value}</dd>
                      </div>
                    ))}
                  </dl>
                ) : null}
                {readable.body ? (
                  // The prose the model wrote, as Markdown — it is written to be
                  // read, and a JSON dump of it is not.
                  <Markdown
                    content={readable.body}
                    stripImages
                    className={cn("mt-1 text-xs", readable.failed && "text-destructive")}
                  />
                ) : null}
              </div>
            );
          })
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
  allowReply?: boolean;
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
export function AgentDiaryNode({
  workOrderId,
  title,
  pageSize = 20,
  height = "md",
  pollMs,
  allowReply = true,
}: DiaryProps) {
  const [entries, setEntries] = useState<WorkOrderEntry[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [reply, setReply] = useState("");
  const [sending, setSending] = useState(false);
  const [replyError, setReplyError] = useState<string | null>(null);
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

  const send = async () => {
    if (!workOrderId || !reply.trim() || sending) return;
    setSending(true);
    setReplyError(null);
    try {
      await replyToWorkOrder(workOrderId, reply.trim());
      setReply("");
      // The reply — and whatever the server decided to do with it — is a diary
      // entry, so reloading is what shows the outcome.
      pinnedToBottom.current = true;
      loadNewest();
    } catch (err: unknown) {
      setReplyError(getApiErrorMessage(err, "Could not send the reply"));
    } finally {
      setSending(false);
    }
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
      {allowReply ? (
        <div className="space-y-1">
          {replyError ? <p className="text-xs text-destructive">{replyError}</p> : null}
          <div className="flex items-end gap-2">
            <textarea
              value={reply}
              onChange={(e) => setReply(e.target.value)}
              onKeyDown={(e) => {
                // Enter sends, Shift+Enter breaks the line — and preventDefault
                // matters twice over, since this sits inside the renderer's form.
                if (e.key !== "Enter" || e.shiftKey) return;
                e.preventDefault();
                void send();
              }}
              placeholder="Reply to the agent…"
              aria-label="Reply to the agent"
              rows={2}
              className="w-full rounded-md border bg-background px-2 py-1.5 text-sm"
            />
            <Button
              type="button"
              size="sm"
              onClick={() => void send()}
              disabled={sending || !reply.trim()}
            >
              {sending ? <Loader2 className="h-3 w-3 animate-spin" /> : "Send"}
            </Button>
          </div>
        </div>
      ) : null}
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
  /** Also list questions an agent is blocked on. On by default: an agent waiting
   *  for an answer is stopped just as hard as one waiting for an approval, and
   *  the two were only ever separate because they arrived in different releases. */
  includeQuestions?: boolean;
}

export function ApprovalQueueNode({
  scope,
  title,
  hideWhenEmpty = true,
  pollMs,
  runIds,
  includeQuestions = true,
}: ApprovalQueueProps) {
  const [items, setItems] = useState<Approval[]>([]);
  const [asks, setAsks] = useState<AgentQuestion[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});

  const mine = useCallback(
    <T extends { run_id: string }>(all: T[]) =>
      scope === "org" || !runIds ? all : all.filter((r) => runIds.has(r.run_id)),
    [scope, runIds],
  );

  const load = useCallback(() => {
    void listApprovals()
      .then((all) => setItems(mine(all)))
      .catch(() => setItems([]));
    if (!includeQuestions) return;
    void listQuestions()
      // Only questions aimed at a person: an agent-to-agent consult is answered by
      // the peer's own run, and showing it here would invite someone to answer for it.
      .then((all) => setAsks(mine(all.filter((q) => q.audience === "human" && q.status === "pending"))))
      .catch(() => setAsks([]));
  }, [mine, includeQuestions]);

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

  const settle = async (id: string, answer: string | null) => {
    setBusy(id);
    setError(null);
    try {
      await (answer === null ? declineQuestion(id) : answerQuestion(id, answer));
      setDrafts((prev) => ({ ...prev, [id]: "" }));
      load();
    } catch {
      setError("That question was already answered elsewhere.");
      load();
    } finally {
      setBusy(null);
    }
  };

  if (items.length === 0 && asks.length === 0 && hideWhenEmpty) return null;

  return (
    <div className="space-y-2">
      {title ? <h3 className="text-sm font-medium">{title}</h3> : null}
      {error ? <p className="text-xs text-destructive">{error}</p> : null}
      {/* Questions first: an agent that asked something is stopped dead until you
          answer, and it has already told you exactly what it needs. */}
      {asks.map((q) => (
        <div key={q.id} className="space-y-2 rounded-md border bg-amber-50/50 p-3 dark:bg-amber-950/20">
          <div className="text-sm font-medium">{q.asked_by ? `${q.asked_by} is asking you` : "An agent is asking you"}</div>
          {/* Agent-authored. Images stripped for the same reason as the diary. */}
          <Markdown content={q.question} stripImages className="text-sm" />
          {q.context ? <Markdown content={q.context} stripImages className="text-xs text-muted-foreground" /> : null}
          <textarea
            value={drafts[q.id] ?? ""}
            onChange={(e) => setDrafts((prev) => ({ ...prev, [q.id]: e.target.value }))}
            placeholder="Your answer…"
            aria-label={`Answer ${q.asked_by ?? "the agent"}`}
            rows={3}
            className="w-full rounded-md border bg-background px-2 py-1.5 text-sm"
          />
          <div className="flex gap-2">
            <Button
              size="sm"
              disabled={busy === q.id || !(drafts[q.id] ?? "").trim()}
              onClick={() => void settle(q.id, (drafts[q.id] ?? "").trim())}
            >
              Answer
            </Button>
            {/* Declining is not ignoring: the agent is unblocked and told to use its
                own judgement, which is better than leaving it parked forever. */}
            <Button size="sm" variant="outline" disabled={busy === q.id} onClick={() => void settle(q.id, null)}>
              Let it decide
            </Button>
          </div>
        </div>
      ))}
      {items.length === 0 && asks.length === 0 ? (
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
// work_order_create
// ------------------------------------------------------------------ //

interface CreateProps {
  title?: string | null;
  submitLabel?: string;
  defaultPriority?: string;
  showAssignee?: boolean;
  detailViewId?: string | null;
}

export function WorkOrderCreateNode({
  title,
  submitLabel = "File it",
  defaultPriority = "normal",
  showAssignee = true,
  detailViewId,
}: CreateProps) {
  const router = useRouter();
  const [heading, setHeading] = useState("");
  const [body, setBody] = useState("");
  const [priority, setPriority] = useState(defaultPriority);
  const [assignee, setAssignee] = useState("");
  const [agents, setAgents] = useState<Agent[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!showAssignee) return;
    void listAgents()
      .then((all) => setAgents(all.filter((a) => a.enabled)))
      .catch(() => setAgents([]));
  }, [showAssignee]);

  const submit = async () => {
    if (!heading.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      const created = await createWorkOrder({
        title: heading.trim(),
        body: body.trim() || null,
        priority,
        assigned_agent_id: assignee || null,
      });
      setHeading("");
      setBody("");
      setAssignee("");
      // Straight to the order that was just filed — the next thing anyone wants
      // is to start it, and hunting for it in the list is friction.
      if (detailViewId) {
        router.push(`/views/${detailViewId}/view?record_id=${encodeURIComponent(created.id)}`);
      }
    } catch (err: unknown) {
      setError(getApiErrorMessage(err, "Could not file the work order"));
    } finally {
      setBusy(false);
    }
  };

  // Not a <form>: every element renders inside the FormRenderer's own form, and
  // HTML forbids nesting them — React reports it as a hydration error and the
  // browser resolves it by dropping the inner form's fields. Enter is wired by
  // hand so the keyboard still files the order.
  return (
    <div className="space-y-2 rounded-md border p-3">
      {title ? <h3 className="text-sm font-medium">{title}</h3> : null}
      {error ? <p className="text-xs text-destructive">{error}</p> : null}
      <input
        value={heading}
        onChange={(e) => setHeading(e.target.value)}
        onKeyDown={(e) => {
          if (e.key !== "Enter") return;
          // Without this the keypress reaches the outer form and submits *that*.
          e.preventDefault();
          void submit();
        }}
        placeholder="What needs doing?"
        aria-label="Work order title"
        className="w-full rounded-md border bg-background px-2 py-1.5 text-sm"
      />
      <textarea
        value={body}
        onChange={(e) => setBody(e.target.value)}
        placeholder="Any detail the agent needs — this is the brief it works from."
        aria-label="Work order detail"
        rows={3}
        className="w-full rounded-md border bg-background px-2 py-1.5 text-sm"
      />
      <div className="flex flex-wrap items-center gap-2">
        <select
          value={priority}
          onChange={(e) => setPriority(e.target.value)}
          aria-label="Priority"
          className="rounded-md border bg-background px-2 py-1.5 text-sm"
        >
          {["low", "normal", "high", "urgent"].map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
        {showAssignee ? (
          <select
            value={assignee}
            onChange={(e) => setAssignee(e.target.value)}
            aria-label="Assign to"
            className="min-w-40 rounded-md border bg-background px-2 py-1.5 text-sm"
          >
            {/* Unassigned is a real choice, not a missing one: it files a request
                for a person rather than queuing an agent. */}
            <option value="">Unassigned</option>
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
        ) : null}
        <Button
          type="button"
          size="sm"
          onClick={() => void submit()}
          disabled={busy || !heading.trim()}
          className="ml-auto"
        >
          {submitLabel}
        </Button>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ //
// work_order_tasks
// ------------------------------------------------------------------ //

interface TasksProps {
  workOrderId: string | null;
  title?: string | null;
  showProgress?: boolean;
  pollMs?: number | null;
}

/** Task status → dot colour. `blocked` is red rather than amber: a blocked task
 *  needs someone, where a pending one is merely not started. */
const TASK_TONE: Record<string, string> = {
  done: "bg-emerald-500",
  in_progress: "bg-blue-500",
  blocked: "bg-destructive",
  carried: "bg-muted-foreground",
  pending: "bg-slate-400",
};

export function WorkOrderTasksNode({ workOrderId, title, showProgress = true, pollMs }: TasksProps) {
  const [wo, setWo] = useState<WorkOrderDetail | null>(null);

  const load = useCallback(() => {
    if (!workOrderId) return;
    void getWorkOrder(workOrderId)
      .then(setWo)
      .catch(() => setWo(null));
  }, [workOrderId]);

  useEffect(load, [load]);
  usePoll(load, pollMs);

  if (!workOrderId) return <p className="text-sm text-muted-foreground">No work order selected.</p>;
  if (!wo) return null;

  // The server's figure, not a recount: two places computing "percent complete"
  // disagree the moment the rule (carried tasks are excluded) changes.
  const pct = Math.round((wo.progress ?? 0) * 100);

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        {title ? <h3 className="text-sm font-medium">{title}</h3> : <span />}
        {showProgress ? <span className="text-xs text-muted-foreground">{pct}% complete</span> : null}
      </div>
      {showProgress ? (
        <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
          <div className="h-full rounded-full bg-primary transition-all" style={{ width: `${pct}%` }} />
        </div>
      ) : null}
      {wo.tasks.length === 0 ? (
        <p className="text-sm text-muted-foreground">No tasks yet.</p>
      ) : (
        <ul className="space-y-1">
          {wo.tasks.map((t) => (
            <li key={t.id} className="flex items-center gap-2 text-sm">
              <span className={cn("h-2 w-2 shrink-0 rounded-full", TASK_TONE[t.status] ?? "bg-muted")} />
              <span className="shrink-0 text-xs text-muted-foreground">{t.key}</span>
              <span className={cn("min-w-0 flex-1 truncate", t.status === "done" && "line-through opacity-60")}>
                {t.title}
              </span>
              <span className="shrink-0 text-[10px] text-muted-foreground">{t.status}</span>
            </li>
          ))}
        </ul>
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
  showAssignee?: boolean;
  showMode?: boolean;
}

/** What each mode means to the person picking it. Wording over jargon: "plan"
 *  and "automatic" say nothing on their own about what the agent may touch. */
const MODE_LABELS: Record<string, string> = {
  plan: "Plan only",
  manual: "Ask me first",
  automatic: "Automatic",
};

const MODE_HELP: Record<string, string> = {
  plan: "Reads, researches and writes a plan. Cannot change anything.",
  manual: "Works the order, pausing for your approval on outbound actions.",
  automatic: "Works the order and approves its own actions. Nobody is asked.",
};

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

export function WorkOrderActionsNode({
  workOrderId,
  title,
  showSummary = true,
  showAssignee = true,
  showMode = true,
}: ActionsProps) {
  const [wo, setWo] = useState<WorkOrder | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    if (!workOrderId) return;
    void getWorkOrder(workOrderId)
      .then(setWo)
      .catch(() => setWo(null));
  }, [workOrderId]);

  useEffect(load, [load]);

  useEffect(() => {
    if (!showAssignee) return;
    void listAgents()
      .then((all) => setAgents(all.filter((a) => a.enabled)))
      .catch(() => setAgents([]));
  }, [showAssignee]);

  const reassign = async (agentId: string) => {
    if (!workOrderId) return;
    setBusy(true);
    setError(null);
    try {
      setWo(await assignWorkOrder(workOrderId, agentId || null));
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Could not change the assignee"));
    } finally {
      setBusy(false);
    }
  };

  const changeMode = async (mode: string) => {
    if (!workOrderId) return;
    setBusy(true);
    setError(null);
    try {
      setWo(await setWorkOrderMode(workOrderId, mode as WorkOrderMode));
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Could not change the mode"));
    } finally {
      setBusy(false);
    }
  };

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
      <div className="flex flex-wrap items-center gap-2">
        {showAssignee ? (
          <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
            Assigned to
            <select
              value={wo.assigned_agent_id ?? ""}
              onChange={(e) => void reassign(e.target.value)}
              disabled={busy}
              aria-label="Assigned agent"
              className="min-w-40 rounded-md border bg-background px-2 py-1 text-sm text-foreground"
            >
              {/* Unassigned is a real state: the order is a request nobody has
                  picked up, and "Start work" on one does nothing. */}
              <option value="">Unassigned</option>
              {agents.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        {showMode ? (
          <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
            Mode
            <select
              value={wo.mode}
              onChange={(e) => void changeMode(e.target.value)}
              disabled={busy}
              aria-label="Agent mode"
              className="rounded-md border bg-background px-2 py-1 text-sm text-foreground"
            >
              {["plan", "manual", "automatic"].map((m) => (
                <option key={m} value={m}>
                  {MODE_LABELS[m]}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        {(wo.allowed_transitions ?? []).map((s) => (
          <Button key={s} size="sm" variant="outline" disabled={busy} onClick={() => void move(s)}>
            {ACTION_LABELS[s] ?? s}
          </Button>
        ))}
      </div>
      {/* What the mode means, spelled out. "Automatic" reads as a convenience
          setting until you know it means nobody is asked before an agent acts. */}
      {showMode ? <p className="text-xs text-muted-foreground">{MODE_HELP[wo.mode]}</p> : null}
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
