"use client";

import { Bot, ClipboardList, Inbox, MessageSquare, Network, Pencil, Plug, Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { AgentDialog } from "@/components/agents/AgentDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  deleteAgent,
  listAgents,
  listProviders,
  setProviderCredential,
  type Agent,
  type ProviderInfo,
} from "@/lib/api/agents";
import { getApiErrorMessage } from "@/lib/api/errors";
import { listWorkflows, type Workflow } from "@/lib/api/workflows";

export default function AgentsPage() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<Agent | "create" | null>(null);
  const [keyDrafts, setKeyDrafts] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const [a, p, w] = await Promise.all([listAgents(), listProviders(), listWorkflows()]);
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

  const supervisorName = (id: string | null) => agents.find((a) => a.id === id)?.name ?? null;

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Bot className="h-6 w-6" />
        <div className="flex-1">
          <h1 className="text-2xl font-semibold">Agents</h1>
          <p className="text-sm text-muted-foreground">
            A roster of AI agents with authority rules, supervisors, and delegation. Agents can run
            workflows and call MCP servers.
          </p>
        </div>
        <Link href="/agents/org">
          <Button size="sm" variant="outline">
            <Network className="h-4 w-4" /> Org chart
          </Button>
        </Link>
        <Link href="/agents/work-orders">
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
                    onChange={(e) => setKeyDrafts((d) => ({ ...d, [p.name]: e.target.value }))}
                  />
                  <Button size="sm" variant="outline" onClick={() => saveKey(p.name)}>
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
        <p className="text-sm text-muted-foreground">No agents yet. Create your first agent to get started.</p>
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          {agents.map((agent) => (
            <Card key={agent.id}>
              <CardContent className="flex items-start gap-3 pt-6">
                <div className="text-2xl">{agent.avatar ?? "🤖"}</div>
                <div className="flex-1">
                  <div className="flex items-center gap-2">
                    <span className="font-medium">{agent.display_name ?? agent.name}</span>
                    <Badge variant="outline">{agent.kind}</Badge>
                    {agent.enabled ? null : <Badge variant="outline">disabled</Badge>}
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {agent.provider} · {agent.model}
                    {agent.supervisor_id ? ` · reports to ${supervisorName(agent.supervisor_id)}` : ""}
                  </p>
                  {agent.description ? (
                    <p className="mt-1 text-sm text-muted-foreground">{agent.description}</p>
                  ) : null}
                </div>
                <div className="flex flex-col gap-1">
                  <Link href={`/agents/${agent.id}/console`}>
                    <Button size="sm" variant="outline">
                      <MessageSquare className="h-4 w-4" /> Console
                    </Button>
                  </Link>
                  <div className="flex gap-1">
                    <Button size="sm" variant="ghost" onClick={() => setEditing(agent)}>
                      <Pencil className="h-4 w-4" />
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => handleDelete(agent)}>
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

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
