"use client";

import {
  Bot,
  ClipboardList,
  Inbox,
  MessageSquare,
  Network,
  Pencil,
  Plug,
  Plus,
  Trash2,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { AgentActivityBadge } from "@/components/agents/AgentActivityBadge";
import { AgentDialog } from "@/components/agents/AgentDialog";
import { NeedsYouDialog } from "@/components/agents/NeedsYouDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  deleteAgent,
  listAgentActivity,
  listAgents,
  listApprovals,
  listProviders,
  listQuestions,
  setProviderCredential,
  type Agent,
  type AgentActivity,
  type AgentQuestion,
  type Approval,
  type ProviderInfo,
} from "@/lib/api/agents";
import { sortByActivity } from "@/lib/agents/activityOrder";
import { getApiErrorMessage } from "@/lib/api/errors";
import { listWorkflows, type Workflow } from "@/lib/api/workflows";

/** How often the roster re-checks who is busy, while the tab is being looked at. A
 *  run turns over in tens of seconds, so this is fast enough to feel live and slow
 *  enough that a page left open all day is not a load source. */
const ACTIVITY_POLL_MS = 8_000;

export default function AgentsPage() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [activity, setActivity] = useState<Record<string, AgentActivity>>({});
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [editing, setEditing] = useState<Agent | "create" | null>(null);
  const [keyDrafts, setKeyDrafts] = useState<Record<string, string>>({});
  const [answering, setAnswering] = useState<{
    agent: Agent;
    approvals: Approval[];
    questions: AgentQuestion[];
  } | null>(null);

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const [a, p, w] = await Promise.all([
        listAgents(),
        listProviders(),
        listWorkflows(),
      ]);
      setAgents(a);
      setProviders(p);
      setWorkflows(w);
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to load agents"));
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const refreshActivity = useCallback(async () => {
    if (
      typeof document !== "undefined" &&
      document.visibilityState !== "visible"
    )
      return;
    try {
      const rows = await listAgentActivity();
      setActivity(Object.fromEntries(rows.map((r) => [r.agent_id, r])));
    } catch {
      // Nothing to say: a failed poll leaves the last known badges on screen, which
      // is better than blanking them on one dropped request.
    }
  }, []);

  // Live state is polled on its own clock, separately from the roster: the roster
  // changes when someone edits it, this changes on its own while you watch. Gated on
  // visibility so a tab left open overnight stops asking.
  useEffect(() => {
    void refreshActivity();
    const timer = setInterval(() => void refreshActivity(), ACTIVITY_POLL_MS);
    const onVisible = () => void refreshActivity();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [refreshActivity]);

  // The pending items themselves are fetched on demand, not on the poll: they are
  // only ever read inside the dialog, and asking for them every 8 seconds would cost
  // two extra requests per tick to render nothing.
  const openAnswers = useCallback(
    async (
      agent: Agent,
      { reopening = false }: { reopening?: boolean } = {},
    ) => {
      try {
        const [pendingApprovals, pendingQuestions] = await Promise.all([
          listApprovals(),
          listQuestions(),
        ]);
        const mine = {
          agent,
          approvals: pendingApprovals.filter((a) => a.agent_id === agent.id),
          questions: pendingQuestions.filter(
            (q) => q.asked_by_agent_id === agent.id,
          ),
        };
        // The badge is up to one poll out of date, so it can still say "needs you"
        // about something answered seconds ago — in another tab, by a colleague, or
        // by the agent's own run ending. Opening an empty dialog to explain that is
        // a worse answer than correcting the badge and saying so in one line.
        if (mine.approvals.length + mine.questions.length === 0) {
          setAnswering(null);
          setNotice(
            reopening
              ? `Nothing left waiting on ${agent.display_name ?? agent.name}.`
              : `${agent.display_name ?? agent.name} is no longer waiting on you — it was settled a moment ago.`,
          );
          void refreshActivity();
          return;
        }
        setNotice(null);
        setAnswering(mine);
      } catch (e: unknown) {
        setError(
          getApiErrorMessage(e, "Failed to load what this agent is waiting on"),
        );
      }
    },
    [refreshActivity],
  );

  const saveKey = async (provider: string) => {
    const key = keyDrafts[provider]?.trim();
    if (!key) return;
    try {
      await setProviderCredential(provider, key);
      setKeyDrafts((d) => ({ ...d, [provider]: "" }));
      await load();
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to save key"));
    }
  };

  const handleDelete = async (agent: Agent) => {
    if (!confirm(`Delete agent "${agent.name}"?`)) return;
    try {
      await deleteAgent(agent.id);
      await load();
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to delete agent"));
    }
  };

  const supervisorName = (id: string | null) =>
    agents.find((a) => a.id === id)?.name ?? null;

  // Busy first, so the one agent waiting on you is not buried under fourteen asleep.
  const ordered = useMemo(
    () => sortByActivity(agents, activity),
    [agents, activity],
  );

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Bot className="h-6 w-6" />
        <div className="flex-1">
          <h1 className="text-2xl font-semibold">Agents</h1>
          <p className="text-sm text-muted-foreground">
            A roster of AI agents with authority rules, supervisors, and
            delegation. Agents can run workflows and call MCP servers.
          </p>
        </div>
        <Link href="/agents/org">
          <Button size="sm" variant="outline">
            <Network className="h-4 w-4" /> Org chart
          </Button>
        </Link>
        {/* The work-order surface is a view, not a route: it is composed from
            elements an org can rearrange. Linked by slug so the URL is the same in
            every org that configures it. */}
        <Link href="/views/work-orders/view">
          <Button size="sm" variant="outline">
            <ClipboardList className="h-4 w-4" /> Work orders
          </Button>
        </Link>
        <Link href="/agents/mcp-servers">
          <Button size="sm" variant="outline">
            <Plug className="h-4 w-4" /> MCP servers
          </Button>
        </Link>
        <Link href="/agents/approvals">
          <Button size="sm" variant="outline">
            <Inbox className="h-4 w-4" /> Inbox
          </Button>
        </Link>
        <Button size="sm" onClick={() => setEditing("create")}>
          <Plus className="h-4 w-4" /> New agent
        </Button>
      </div>

      {error ? <p className="text-sm text-destructive">{error}</p> : null}
      {notice ? (
        <p className="text-sm text-muted-foreground">{notice}</p>
      ) : null}

      <Card>
        <CardContent className="space-y-2 pt-6">
          <h2 className="text-sm font-medium">Providers</h2>
          <div className="grid gap-2 md:grid-cols-3">
            {providers.map((p) => (
              <div key={p.name} className="rounded-md border p-3">
                <div className="mb-2 flex items-center justify-between">
                  <span className="text-sm font-medium">{p.label}</span>
                  <Badge variant={p.configured ? "default" : "outline"}>
                    {p.configured ? "configured" : "no key"}
                  </Badge>
                </div>
                <div className="flex gap-2">
                  <Input
                    type="password"
                    placeholder="Set org API key"
                    value={keyDrafts[p.name] ?? ""}
                    onChange={(e) =>
                      setKeyDrafts((d) => ({ ...d, [p.name]: e.target.value }))
                    }
                  />
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => saveKey(p.name)}
                  >
                    Save
                  </Button>
                </div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {isLoading ? (
        <Skeleton className="h-40 w-full" />
      ) : agents.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No agents yet. Create your first agent to get started.
        </p>
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          {ordered.map((agent) => (
            <Card key={agent.id}>
              <CardContent className="flex items-start gap-3 pt-6">
                <div className="text-2xl">{agent.avatar ?? "🤖"}</div>
                <div className="flex-1">
                  <div className="flex items-center gap-2">
                    <span className="font-medium">
                      {agent.display_name ?? agent.name}
                    </span>
                    <AgentActivityBadge
                      activity={activity[agent.id]}
                      onAnswer={() => void openAnswers(agent)}
                    />
                    <Badge variant="outline">{agent.kind}</Badge>
                    {agent.enabled ? null : (
                      <Badge variant="outline">disabled</Badge>
                    )}
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {agent.provider} · {agent.model}
                    {agent.supervisor_id
                      ? ` · reports to ${supervisorName(agent.supervisor_id)}`
                      : ""}
                  </p>
                  {agent.description ? (
                    <p className="mt-1 text-sm text-muted-foreground">
                      {agent.description}
                    </p>
                  ) : null}
                </div>
                <div className="flex flex-col gap-1">
                  <Link href={`/agents/${agent.id}/console`}>
                    <Button size="sm" variant="outline">
                      <MessageSquare className="h-4 w-4" /> Console
                    </Button>
                  </Link>
                  <div className="flex gap-1">
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => setEditing(agent)}
                    >
                      <Pencil className="h-4 w-4" />
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => handleDelete(agent)}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {answering ? (
        <NeedsYouDialog
          agent={answering.agent}
          approvals={answering.approvals}
          questions={answering.questions}
          onClose={() => setAnswering(null)}
          onSettled={() => {
            // Re-read both: the dialog's own list shrinks as items settle, and the
            // badge behind it has to stop saying "needs you" once nothing is left.
            void openAnswers(answering.agent, { reopening: true });
            void refreshActivity();
          }}
        />
      ) : null}

      {editing ? (
        <AgentDialog
          editing={editing}
          providers={providers}
          agents={agents}
          workflows={workflows.map((w) => ({ id: w.id, name: w.name }))}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            void load();
          }}
        />
      ) : null}
    </div>
  );
}
