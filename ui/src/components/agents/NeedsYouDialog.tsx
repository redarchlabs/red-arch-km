"use client";

import { Check, X } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import {
  answerQuestion,
  approveApproval,
  declineQuestion,
  denyApproval,
  type Agent,
  type AgentQuestion,
  type Approval,
} from "@/lib/api/agents";
import { getApiErrorMessage } from "@/lib/api/errors";

/**
 * Answer an agent where you noticed it needed you.
 *
 * The badge told you which agent was stuck; before this, acting on it meant leaving
 * for a shared inbox and finding the right row again among everyone else's — enough
 * friction that "I'll deal with it later" won, and later is what left a work order
 * parked for five hours. The same two actions live here, scoped to one agent.
 *
 * Deliberately not a read-only summary with a link: a dialog you cannot act in is a
 * longer route to the inbox, not a shorter one.
 */
export function NeedsYouDialog({
  agent,
  approvals,
  questions,
  onClose,
  onSettled,
}: {
  agent: Agent;
  approvals: Approval[];
  questions: AgentQuestion[];
  onClose: () => void;
  /** Refresh the roster: settling an item changes the badge behind this dialog. */
  onSettled: () => void;
}) {
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const settled = approvals.length + questions.length === 0;

  const decide = async (id: string, approve: boolean) => {
    setBusyId(id);
    setError(null);
    try {
      await (approve ? approveApproval(id) : denyApproval(id));
      setNotice(
        approve
          ? "Approved — the run has picked up where it paused."
          : "Denied.",
      );
      onSettled();
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to record the decision"));
    } finally {
      setBusyId(null);
    }
  };

  const respond = async (id: string, send: boolean) => {
    setBusyId(id);
    setError(null);
    try {
      const result = send
        ? await answerQuestion(id, drafts[id] ?? "")
        : await declineQuestion(id);
      // The agent may have given up while the question sat unanswered. Say so, rather
      // than letting the row vanish and imply it acted on what you typed.
      setNotice(
        result.resumed
          ? send
            ? "Answer sent — the agent has picked up where it left off."
            : "The agent was told to proceed on its own judgement."
          : "Recorded, but the agent had already stopped waiting, so nothing resumed.",
      );
      setDrafts((prev) => ({ ...prev, [id]: "" }));
      onSettled();
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to send the answer"));
    } finally {
      setBusyId(null);
    }
  };

  return (
    <Dialog open onClose={onClose} className="max-w-2xl">
      <DialogHeader>
        <DialogTitle>{agent.display_name ?? agent.name} needs you</DialogTitle>
      </DialogHeader>

      {error ? <p className="text-sm text-destructive">{error}</p> : null}
      {notice ? (
        <p className="text-sm text-muted-foreground">{notice}</p>
      ) : null}

      <div className="max-h-[60vh] space-y-4 overflow-y-auto py-2">
        {settled ? (
          <p className="text-sm text-muted-foreground">
            Nothing left waiting — you can close this.
          </p>
        ) : null}

        {questions.map((q) => (
          <div key={q.id} className="rounded-md border p-3">
            <p className="whitespace-pre-wrap text-sm">{q.question}</p>
            {q.context ? (
              <p className="mt-1 whitespace-pre-wrap text-xs text-muted-foreground">
                {q.context}
              </p>
            ) : null}
            <Textarea
              className="mt-2"
              rows={3}
              placeholder="Your answer…"
              value={drafts[q.id] ?? ""}
              onChange={(e) =>
                setDrafts((d) => ({ ...d, [q.id]: e.target.value }))
              }
            />
            <div className="mt-2 flex gap-2">
              <Button
                size="sm"
                // An empty answer reads to the agent as a real answer meaning nothing,
                // which is worse than declining — so it cannot be sent.
                disabled={busyId === q.id || !(drafts[q.id] ?? "").trim()}
                onClick={() => respond(q.id, true)}
              >
                Send answer
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={busyId === q.id}
                onClick={() => respond(q.id, false)}
              >
                Let it decide
              </Button>
            </div>
          </div>
        ))}

        {approvals.map((a) => (
          <div key={a.id} className="rounded-md border p-3">
            <div className="font-mono text-sm font-medium">{a.tool_name}</div>
            <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap text-xs text-muted-foreground">
              {JSON.stringify(a.arguments, null, 2)}
            </pre>
            <div className="mt-2 flex gap-2">
              <Button
                size="sm"
                disabled={busyId === a.id}
                onClick={() => decide(a.id, true)}
              >
                <Check className="h-4 w-4" /> Approve
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={busyId === a.id}
                onClick={() => decide(a.id, false)}
              >
                <X className="h-4 w-4" /> Deny
              </Button>
            </div>
          </div>
        ))}
      </div>
    </Dialog>
  );
}
