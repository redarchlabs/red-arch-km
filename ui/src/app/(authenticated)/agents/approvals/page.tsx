"use client";

import { ArrowLeft, Bell, Check, MessageCircleQuestion, X } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Markdown } from "@/components/common/Markdown";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import {
  answerQuestion,
  approveApproval,
  declineQuestion,
  denyApproval,
  listApprovals,
  listNotifications,
  listQuestions,
  resolveNotification,
  type AgentQuestion,
  type Approval,
  type Notification,
} from "@/lib/api/agents";
import { getApiErrorMessage } from "@/lib/api/errors";

export default function AgentApprovalsPage() {
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [questions, setQuestions] = useState<AgentQuestion[]>([]);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const [a, q, n] = await Promise.all([
        listApprovals(),
        listQuestions(),
        listNotifications(true),
      ]);
      setApprovals(a);
      setQuestions(q);
      // Escalations and reviews only. Approval and question notifications are the
      // *announcement* of the two sections above — listing them here too asked a
      // person to act on the same item twice, and the second copy came with a
      // "Resolve" button that does nothing but tick it off.
      setNotifications(
        n.filter((row) => row.kind === "escalation" || row.kind === "review"),
      );
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to load inbox"));
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const decide = async (id: string, approve: boolean) => {
    try {
      await (approve ? approveApproval(id) : denyApproval(id));
      await load();
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to record decision"));
    }
  };

  const respond = async (id: string, send: boolean) => {
    setBusyId(id);
    setError(null);
    setNotice(null);
    try {
      const result = send
        ? await answerQuestion(id, drafts[id] ?? "")
        : await declineQuestion(id);
      // The agent may have given up while the question sat in the inbox. Say so
      // rather than letting the row vanish and imply it acted on the answer.
      setNotice(
        result.resumed
          ? send
            ? "Answer sent — the agent has picked up where it left off."
            : "The agent was told to proceed on its own judgement."
          : "Recorded, but the agent had already stopped waiting, so nothing resumed.",
      );
      setDrafts((prev) => ({ ...prev, [id]: "" }));
      await load();
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to send the answer"));
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Link
          href="/agents"
          className="text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-5 w-5" />
        </Link>
        <div className="flex-1">
          <h1 className="text-2xl font-semibold">
            Approvals, questions & escalations
          </h1>
          <p className="text-sm text-muted-foreground">
            Approve or deny the actions agents paused on, answer what they have
            asked you, and clear escalations they raised.
          </p>
        </div>
      </div>

      {error ? <p className="text-sm text-destructive">{error}</p> : null}
      {notice ? (
        <p className="text-sm text-muted-foreground">{notice}</p>
      ) : null}

      <section className="space-y-2">
        <h2 className="text-sm font-medium">Pending approvals</h2>
        {isLoading ? (
          <Skeleton className="h-20 w-full" />
        ) : approvals.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            Nothing waiting on you.
          </p>
        ) : (
          approvals.map((a) => (
            <Card key={a.id}>
              <CardContent className="flex items-center gap-3 pt-6">
                <div className="flex-1">
                  <div className="font-mono text-sm font-medium">
                    {a.tool_name}
                  </div>
                  <pre className="mt-1 overflow-x-auto whitespace-pre-wrap text-xs text-muted-foreground">
                    {JSON.stringify(a.arguments, null, 2)}
                  </pre>
                  {a.workflow_run_id && a.workflow_id ? (
                    <p className="mt-1 text-xs text-muted-foreground">
                      Blocking a workflow step —{" "}
                      <Link
                        href={`/workflows/${a.workflow_id}/runs?run=${a.workflow_run_id}`}
                        className="underline-offset-2 hover:underline"
                      >
                        open the workflow run
                      </Link>
                    </p>
                  ) : null}
                </div>
                <Button size="sm" onClick={() => decide(a.id, true)}>
                  <Check className="h-4 w-4" /> Approve
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => decide(a.id, false)}
                >
                  <X className="h-4 w-4" /> Deny
                </Button>
              </CardContent>
            </Card>
          ))
        )}
      </section>

      <section className="space-y-2">
        <h2 className="flex items-center gap-2 text-sm font-medium">
          <MessageCircleQuestion className="h-4 w-4" /> Questions for you
        </h2>
        {isLoading ? (
          <Skeleton className="h-24 w-full" />
        ) : questions.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No agent is waiting on an answer.
          </p>
        ) : (
          questions.map((q) => (
            <Card key={q.id}>
              <CardContent className="space-y-3 pt-6">
                <div>
                  {/* An agent asking a real question writes a real question:
                      options, trade-offs, what it has already tried. As one flat
                      line that arrived as an unreadable wall — "1) grant... 2)
                      provision... 3) run..." — with the numbers inline and the
                      whole thing wrapped as prose. It was Markdown all along;
                      nothing was rendering it. */}
                  <div className="flex items-start justify-between gap-2">
                    <Markdown
                      content={q.question}
                      // LLM-authored: an image URL a poisoned document talked the
                      // model into emitting would otherwise be fetched by the
                      // reader's browser.
                      stripImages
                      className="min-w-0 flex-1 font-medium"
                    />
                    {q.asked_by ? (
                      <Badge variant="outline" className="shrink-0">
                        {q.asked_by}
                      </Badge>
                    ) : null}
                  </div>
                  {q.context ? (
                    <Markdown
                      content={q.context}
                      stripImages
                      className="mt-1 text-xs text-muted-foreground"
                    />
                  ) : null}
                </div>
                <Textarea
                  rows={3}
                  placeholder="Type your answer — the agent continues from here with it."
                  value={drafts[q.id] ?? ""}
                  onChange={(e) =>
                    setDrafts((prev) => ({ ...prev, [q.id]: e.target.value }))
                  }
                />
                <div className="flex items-center gap-2">
                  <Button
                    size="sm"
                    disabled={busyId === q.id || !(drafts[q.id] ?? "").trim()}
                    onClick={() => respond(q.id, true)}
                  >
                    <Check className="h-4 w-4" /> Send answer
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busyId === q.id}
                    onClick={() => respond(q.id, false)}
                  >
                    Can&apos;t answer
                  </Button>
                  <span className="text-xs text-muted-foreground">
                    The run is paused until one of these.
                  </span>
                </div>
              </CardContent>
            </Card>
          ))
        )}
      </section>

      <section className="space-y-2">
        <h2 className="flex items-center gap-2 text-sm font-medium">
          <Bell className="h-4 w-4" /> Escalations & reviews
        </h2>
        {isLoading ? (
          <Skeleton className="h-16 w-full" />
        ) : notifications.length === 0 ? (
          <p className="text-sm text-muted-foreground">No open escalations.</p>
        ) : (
          notifications.map((n) => (
            <Card key={n.id}>
              <CardContent className="flex items-start gap-3 pt-6">
                <div className="flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium">{n.title}</span>
                    <Badge variant="outline">{n.kind}</Badge>
                  </div>
                  {n.body ? (
                    <Markdown
                      content={n.body}
                      stripImages
                      className="mt-1 text-muted-foreground"
                    />
                  ) : null}
                </div>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={async () => {
                    await resolveNotification(n.id);
                    await load();
                  }}
                >
                  Resolve
                </Button>
              </CardContent>
            </Card>
          ))
        )}
      </section>
    </div>
  );
}
