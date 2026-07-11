/**
 * Agents API — the multi-provider agent org (roster, providers, console, runs).
 *
 * Note: `agent.ts` is the older single config-assistant client; this module is
 * the new plural surface. CRUD goes through the shared axios client; the console
 * uses fetch+SSE (EventSource can't send auth/org headers), mirroring `agent.ts`.
 */

import { getToken } from "@/lib/auth/clerk";

import apiClient from "./client";

export type AgentKind = "coordinator" | "advisory" | "operator";

export interface AgentGrants {
  tools: string[];
  records_write: boolean;
  approval_required: string[];
}

export interface Agent {
  id: string;
  name: string;
  display_name: string | null;
  description: string | null;
  kind: AgentKind;
  persona: string | null;
  provider: string;
  model: string;
  params: Record<string, unknown>;
  supervisor_id: string | null;
  avatar: string | null;
  accent: string | null;
  enabled: boolean;
  grants: AgentGrants;
  mcp_server_ids: string[];
  workflow_allowlist: string[];
  created_at: string;
  updated_at: string;
}

export interface ProviderModel {
  id: string;
  label: string;
}

export interface ProviderInfo {
  name: string;
  label: string;
  models: ProviderModel[];
  key_env: string;
  configured: boolean;
}

export interface AgentCreateInput {
  name: string;
  display_name?: string | null;
  description?: string | null;
  kind: AgentKind;
  persona?: string | null;
  provider: string;
  model: string;
  supervisor_id?: string | null;
  enabled?: boolean;
  grants?: AgentGrants;
  mcp_server_ids?: string[];
  workflow_allowlist?: string[];
}

export type AgentUpdateInput = Partial<Omit<AgentCreateInput, "name">>;

export interface AgentRun {
  id: string;
  agent_id: string | null;
  work_order_id: string | null;
  parent_run_id: string | null;
  status: string;
  trigger: string;
  wait_kind: string | null;
  provider: string | null;
  model: string | null;
  label: string | null;
  error: string | null;
  total_tokens: number;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
}

export async function listAgents(): Promise<Agent[]> {
  return (await apiClient.get<Agent[]>("/agents/")).data;
}

export async function getAgent(id: string): Promise<Agent> {
  return (await apiClient.get<Agent>(`/agents/${id}`)).data;
}

export async function createAgent(input: AgentCreateInput): Promise<Agent> {
  return (await apiClient.post<Agent>("/agents/", input)).data;
}

export async function updateAgent(id: string, input: AgentUpdateInput): Promise<Agent> {
  return (await apiClient.patch<Agent>(`/agents/${id}`, input)).data;
}

export async function deleteAgent(id: string): Promise<void> {
  await apiClient.delete(`/agents/${id}`);
}

export async function listProviders(): Promise<ProviderInfo[]> {
  return (await apiClient.get<ProviderInfo[]>("/agents/providers")).data;
}

export async function setProviderCredential(provider: string, apiKey: string): Promise<void> {
  await apiClient.post("/agents/providers/credentials", { provider, api_key: apiKey });
}

export async function deleteProviderCredential(provider: string): Promise<void> {
  await apiClient.delete(`/agents/providers/${provider}/credentials`);
}

export async function listAgentRuns(agentId: string): Promise<AgentRun[]> {
  return (await apiClient.get<AgentRun[]>(`/agents/${agentId}/runs`)).data;
}

export interface Approval {
  id: string;
  run_id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  status: string;
  decided_at: string | null;
  created_at: string;
}

export interface Notification {
  id: string;
  kind: string;
  run_id: string | null;
  work_order_id: string | null;
  recipient_role: string | null;
  title: string;
  body: string | null;
  status: string;
  created_at: string;
}

export async function listApprovals(): Promise<Approval[]> {
  return (await apiClient.get<Approval[]>("/agents/approvals")).data;
}

export async function approveApproval(id: string): Promise<Approval> {
  return (await apiClient.post<Approval>(`/agents/approvals/${id}/approve`)).data;
}

export async function denyApproval(id: string): Promise<Approval> {
  return (await apiClient.post<Approval>(`/agents/approvals/${id}/deny`)).data;
}

export async function listNotifications(unresolvedOnly = false): Promise<Notification[]> {
  return (
    await apiClient.get<Notification[]>("/agents/notifications", {
      params: { unresolved_only: unresolvedOnly },
    })
  ).data;
}

export async function resolveNotification(id: string): Promise<Notification> {
  return (await apiClient.post<Notification>(`/agents/notifications/${id}/resolve`)).data;
}

/** Events streamed by the interactive agent console over SSE. */
export type AgentConsoleEvent =
  | { type: "run_started"; run_id: string }
  | { type: "delta"; content: string }
  | { type: "tool_call"; id?: string; name: string; arguments: Record<string, unknown> }
  | { type: "tool_result"; name: string; result: Record<string, unknown> }
  | { type: "approval_required"; name: string; arguments: Record<string, unknown> }
  | { type: "usage"; prompt_tokens: number; completion_tokens: number; total_tokens: number }
  | { type: "done"; truncated?: boolean }
  | { type: "error"; error: string };

export interface AgentConsoleMessage {
  role: "user" | "assistant";
  content: string;
}

/** Stream the interactive agent console. Mirrors streamConfigAgent in agent.ts. */
export async function* streamAgentConsole(
  agentId: string,
  messages: AgentConsoleMessage[],
  options: { signal?: AbortSignal } = {},
): AsyncGenerator<AgentConsoleEvent> {
  const baseUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";
  const orgId = typeof window !== "undefined" ? localStorage.getItem("redarch:currentOrgId") : null;
  const token = await getToken();

  const response = await fetch(`${baseUrl}/agents/${agentId}/console/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(orgId ? { "X-Org-ID": orgId } : {}),
    },
    body: JSON.stringify({ messages }),
    signal: options.signal,
  });

  if (!response.ok) {
    throw new Error(`Console request failed: ${response.status}`);
  }
  if (!response.body) {
    throw new Error("Console stream has no body");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const frames = buffer.split("\n\n");
      buffer = frames.pop() ?? "";
      for (const frame of frames) {
        const trimmed = frame.trim();
        if (!trimmed.startsWith("data:")) continue;
        const payload = trimmed.slice(5).trim();
        if (!payload) continue;
        try {
          yield JSON.parse(payload) as AgentConsoleEvent;
        } catch {
          // Ignore malformed frames rather than tearing down the stream.
        }
      }
    }
  } finally {
    try {
      await reader.cancel();
    } catch {
      // Reader may already be released.
    }
  }
}
