/**
 * Agents API — the multi-provider agent org (roster, providers, console, runs).
 *
 * Note: `agent.ts` is the older single config-assistant client; this module is
 * the new plural surface. CRUD goes through the shared axios client; the console
 * uses fetch+SSE (EventSource can't send auth/org headers), mirroring `agent.ts`.
 */

import { pendingWorkChanged } from "@/lib/agents/pendingWork";
import { authHeaders } from "@/lib/auth/headers";

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
  /** Which workflows may bind this agent to an agent_task step (ids, or ["*"]). */
  workflow_invocable: string[];
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
  workflow_invocable?: string[];
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
  /** Workflow agent_task linkage (null for console/schedule/delegation runs). */
  workflow_run_id: string | null;
  workflow_node_id: string | null;
  /** The schema-validated complete_task object. */
  output: Record<string, unknown> | null;
}

export interface AgentRunStep {
  id: string;
  run_id: string;
  seq: number;
  kind: string;
  name: string | null;
  content: Record<string, unknown>;
  tokens: number | null;
  created_at: string;
}

export async function listAgents(): Promise<Agent[]> {
  return (await apiClient.get<Agent[]>("/agents/")).data;
}

/** What an agent is doing right now. `working` is informational; `needs_you` means a
 *  person is the blocker — an approval or a question — and wins when both are true. */
export interface AgentActivity {
  agent_id: string;
  state: "working" | "needs_you";
  live_runs: number;
  waiting_on_you: number;
}

/** Only agents with something going on come back, so an empty list means idle. */
export async function listAgentActivity(): Promise<AgentActivity[]> {
  return (await apiClient.get<AgentActivity[]>("/agents/activity")).data;
}

export async function getAgentRun(runId: string): Promise<AgentRun> {
  return (await apiClient.get<AgentRun>(`/agents/runs/${runId}`)).data;
}

/** Transcript of a (possibly background) agent run — powers the workflow run
 * monitor's inline "what is the agent doing" view. */
export async function listAgentRunSteps(
  runId: string,
): Promise<AgentRunStep[]> {
  return (await apiClient.get<AgentRunStep[]>(`/agents/runs/${runId}/steps`))
    .data;
}

export async function getAgent(id: string): Promise<Agent> {
  return (await apiClient.get<Agent>(`/agents/${id}`)).data;
}

export async function createAgent(input: AgentCreateInput): Promise<Agent> {
  return (await apiClient.post<Agent>("/agents/", input)).data;
}

export async function updateAgent(
  id: string,
  input: AgentUpdateInput,
): Promise<Agent> {
  return (await apiClient.patch<Agent>(`/agents/${id}`, input)).data;
}

export async function deleteAgent(id: string): Promise<void> {
  await apiClient.delete(`/agents/${id}`);
}

export async function listProviders(): Promise<ProviderInfo[]> {
  return (await apiClient.get<ProviderInfo[]>("/agents/providers")).data;
}

export async function setProviderCredential(
  provider: string,
  apiKey: string,
): Promise<void> {
  await apiClient.post("/agents/providers/credentials", {
    provider,
    api_key: apiKey,
  });
}

export async function deleteProviderCredential(
  provider: string,
): Promise<void> {
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
  /** Set when the parked run is a workflow agent_task step — deep-link target
   * (/workflows/{workflow_id}/runs?run={workflow_run_id}). */
  workflow_run_id: string | null;
  workflow_id: string | null;
  /** Whose run is parked on this — lets a roster card claim its own approvals. */
  agent_id: string | null;
  agent_name: string | null;
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

// The four settle calls below announce on success — never before, so a failed
// decision cannot make the bell drop a count that is still outstanding. Doing it
// here rather than at each call site means every surface that settles work (this
// page, the work-order panel, the roster dialog, the workflow run panel) keeps
// the header honest without having to remember to.
export async function approveApproval(id: string): Promise<Approval> {
  const approval = (
    await apiClient.post<Approval>(`/agents/approvals/${id}/approve`)
  ).data;
  pendingWorkChanged();
  return approval;
}

export async function denyApproval(id: string): Promise<Approval> {
  const approval = (await apiClient.post<Approval>(`/agents/approvals/${id}/deny`))
    .data;
  pendingWorkChanged();
  return approval;
}

export async function listNotifications(
  unresolvedOnly = false,
): Promise<Notification[]> {
  return (
    await apiClient.get<Notification[]>("/agents/notifications", {
      params: { unresolved_only: unresolvedOnly },
    })
  ).data;
}

export async function resolveNotification(id: string): Promise<Notification> {
  const notification = (
    await apiClient.post<Notification>(`/agents/notifications/${id}/resolve`)
  ).data;
  pendingWorkChanged();
  return notification;
}

/** An agent is blocked waiting for an answer. Unlike an approval — which is a
 * yes/no on an action the agent already chose — the text typed here becomes the
 * result of the tool call that blocked, and the run continues from that point. */
export interface AgentQuestion {
  id: string;
  run_id: string;
  audience: string;
  question: string;
  context: string | null;
  answer: string | null;
  status: string;
  answered_at: string | null;
  created_at: string;
  asked_by: string | null;
  target_agent: string | null;
  /** The id behind `asked_by` — lets a roster card pick out its own questions. */
  asked_by_agent_id: string | null;
}

export interface AnswerResult {
  question: AgentQuestion;
  /** False when the asking run had already ended — the answer is recorded, but no
   * agent picked it up. */
  resumed: boolean;
}

/** Only questions addressed to a person; peer consults are answered by the
 * consulted agent, not from here. */
export async function listQuestions(): Promise<AgentQuestion[]> {
  return (await apiClient.get<AgentQuestion[]>("/agents/questions")).data;
}

export async function answerQuestion(
  id: string,
  answer: string,
): Promise<AnswerResult> {
  const result = (
    await apiClient.post<AnswerResult>(`/agents/questions/${id}/answer`, {
      answer,
    })
  ).data;
  pendingWorkChanged();
  return result;
}

/** Unblock the agent without answering — it is told to use its own judgement. */
export async function declineQuestion(
  id: string,
  reason?: string,
): Promise<AnswerResult> {
  const result = (
    await apiClient.post<AnswerResult>(`/agents/questions/${id}/decline`, {
      reason: reason || null,
    })
  ).data;
  pendingWorkChanged();
  return result;
}

/** Events streamed by the interactive agent console over SSE. */
export type AgentConsoleEvent =
  | { type: "run_started"; run_id: string }
  | { type: "delta"; content: string }
  | {
      type: "tool_call";
      id?: string;
      name: string;
      arguments: Record<string, unknown>;
    }
  | { type: "tool_result"; name: string; result: Record<string, unknown> }
  | {
      type: "approval_required";
      name: string;
      arguments: Record<string, unknown>;
    }
  | {
      type: "usage";
      prompt_tokens: number;
      completion_tokens: number;
      total_tokens: number;
    }
  /** The agent asked something and is waiting on the answer. The stream ends here —
   * an answer takes as long as a person takes — and the run continues in the
   * background once the question is answered from the inbox. */
  | {
      type: "waiting";
      wait_kind: string;
      run_id: string;
      question_id?: string;
      question?: string;
      peer?: string;
    }
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
  options: { signal?: AbortSignal; documentIds?: string[] } = {},
): AsyncGenerator<AgentConsoleEvent> {
  const baseUrl =
    process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";
  const orgId =
    typeof window !== "undefined"
      ? localStorage.getItem("redarch:currentOrgId")
      : null;
  const auth = await authHeaders();

  const response = await fetch(`${baseUrl}/agents/${agentId}/console/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...auth,
      ...(orgId ? { "X-Org-ID": orgId } : {}),
    },
    body: JSON.stringify({ messages, document_ids: options.documentIds ?? [] }),
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
