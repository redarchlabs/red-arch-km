"use client";

import { Bot, Check, RefreshCw, X } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  approveApproval,
  denyApproval,
  getAgentRun,
  listAgentRunSteps,
  listApprovals,
  type AgentRun,
  type AgentRunStep,
  type Approval,
} from "@/lib/api/agents";
import { getApiErrorMessage } from "@/lib/api/errors";

/**
 * Inline view of the agent run a workflow step is parked on: live status, the
 * transcript (what the agent is doing), and any pending approval blocking it —
 * approve/deny HERE, so the operator watching the workflow sees one queue, not
 * two disconnected inboxes.
 */
export function AgentRunPanel({
  agentRunId,
  onActed,
}: {
  agentRunId: string;
  /** Called after an approval decision (the parent refreshes the run list). */
  onActed?: () => void;
}) {
  const [run, setRun] = useState<AgentRun | null>(null);
  const [steps, setSteps] = useState<AgentRunStep[] | null>(null);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [showTranscript, setShowTranscript] = useState(false);

  const load = useCallback(async () => {
    try {
      const [r, s, a] = await Promise.all([
        getAgentRun(agentRunId),
        listAgentRunSteps(agentRunId),
        listApprovals().catch(() => [] as Approval[]),
      ]);
      setRun(r);
      setSteps(s);
      setApprovals(a.filter((ap) => ap.run_id === agentRunId && ap.status === "pending"));
      setError(null);
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to load the agent run"));
    }
  }, [agentRunId]);

  useEffect(() => {
    void load();
  }, [load]);

  const decide = async (approval: Approval, approved: boolean) => {
    setBusy(approval.id);
    try {
      await (approved ? approveApproval(approval.id) : denyApproval(approval.id));
      await load();
      onActed?.();
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to record the decision"));
    } finally {
      setBusy(null);
    }
  };

  if (run === null) return <Skeleton className="h-10 w-full" />;

  return (
    <div className="rounded-md border border-sky-500/30 bg-sky-500/10 p-2 text-xs">
      <div className="flex items-center gap-2">
        <Bot className="h-4 w-4 text-sky-600 dark:text-sky-400" />
        <span className="font-medium text-sky-700 dark:text-sky-300">
          {run.status === "queued"
            ? "Agent queued — the worker picks it up on the next sweep."
            : run.status === "running"
              ? "Agent working…"
              : run.status === "waiting"
                ? "Agent paused — waiting for an approval below."
                : `Agent run ${run.status}`}
        </span>
        <Badge variant="outline" className="ml-auto">
          {run.total_tokens} tok
        </Badge>
        <Button variant="ghost" size="icon" className="h-6 w-6" onClick={() => void load()} aria-label="Refresh agent run">
          <RefreshCw className="h-3.5 w-3.5" />
        </Button>
      </div>
      {run.error ? <p className="mt-1 text-destructive">{run.error}</p> : null}

      {approvals.map((approval) => (
        <div key={approval.id} className="mt-2 rounded-md border border-amber-500/40 bg-amber-500/10 p-2">
          <p className="font-medium text-amber-700 dark:text-amber-300">
            Approval needed: <code>{approval.tool_name}</code>
          </p>
          <pre className="mt-1 max-h-24 overflow-auto text-[11px] text-muted-foreground">
            {JSON.stringify(approval.arguments, null, 2)}
          </pre>
          <div className="mt-1.5 flex items-center gap-2">
            <Button size="sm" disabled={busy !== null} onClick={() => void decide(approval, true)}>
              <Check className="h-3.5 w-3.5" /> Approve
            </Button>
            <Button size="sm" variant="outline" disabled={busy !== null} onClick={() => void decide(approval, false)}>
              <X className="h-3.5 w-3.5" /> Deny
            </Button>
          </div>
        </div>
      ))}

      <div className="mt-2 flex items-center gap-3">
        <Button variant="ghost" size="sm" className="h-6 px-2 text-xs" onClick={() => setShowTranscript((v) => !v)}>
          {showTranscript ? "Hide transcript" : `Transcript (${steps?.length ?? 0})`}
        </Button>
        <Link href="/agents/approvals" className="text-xs text-muted-foreground underline-offset-2 hover:underline">
          Approvals inbox
        </Link>
      </div>
      {showTranscript && steps ? (
        <div className="mt-1 max-h-48 space-y-1 overflow-auto">
          {steps.map((step) => (
            <TranscriptRow key={step.id} step={step} />
          ))}
        </div>
      ) : null}
      {error ? <p className="mt-1 text-destructive">{error}</p> : null}
    </div>
  );
}

function TranscriptRow({ step }: { step: AgentRunStep }) {
  const summary =
    step.kind === "tool_call"
      ? `→ ${step.name}(${JSON.stringify(step.content.arguments ?? {})})`
      : step.kind === "tool_result"
        ? `← ${step.name}`
        : step.kind === "assistant"
          ? String(step.content.content ?? step.content.output ?? "")
          : step.kind === "approval_required"
            ? `⏸ approval requested: ${step.name}`
            : step.kind === "escalation"
              ? `⤴ escalated: ${String(step.content.reason ?? "")}`
              : JSON.stringify(step.content);
  return (
    <div className="rounded bg-background/70 px-2 py-1 font-mono text-[11px] text-muted-foreground">
      <span className="mr-1 font-semibold">{step.kind}</span>
      <span className="break-all">{summary.slice(0, 400)}</span>
    </div>
  );
}
