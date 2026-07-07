import { getToken } from "@/lib/auth/clerk";

import apiClient from "./client";

export interface SearchHit {
  id: string;
  score: number;
  text: string;
  document_id: string;
  document_key: string;
  document_title: string;
  chunk_order: number;
}

export interface SearchResponse {
  hits: SearchHit[];
  total: number;
}

export interface ChatSource {
  document_id: string;
  document_key: string;
  document_title: string;
  score: number;
  /** 1-based citation number; inline [n] markers in the answer map to this. */
  number?: number;
  /** Heading path of the cited passage (e.g. "Chapter 1 › Intro"); null/absent for unstructured text. */
  section?: string | null;
  /** Index of the cited chunk within the document; used to deep-link to the passage. */
  chunk_order?: number | null;
  /** Trimmed preview of the passage the citation came from. */
  snippet?: string;
}

export interface StreamEvent {
  type: "sources" | "graph" | "delta" | "done" | "error";
  sources?: ChatSource[];
  triplets?: Array<Record<string, string>>;
  content?: string;
  message?: string;
}

export async function searchDocuments(
  query: string,
  limit = 5,
  tags: string[] = [],
): Promise<SearchResponse> {
  const response = await apiClient.post<SearchResponse>("/search/", { query, limit, tags });
  return response.data;
}

/** One step in the agent's reasoning trace, shown under the answer. */
export interface AgentTraceStep {
  type: "thought" | "tool_call" | "tool_result";
  content?: string;
  tool?: string;
  args?: Record<string, unknown>;
  recordCount?: number;
}

/** Raw SSE events emitted by the agentic (fact-engine) chat stream. */
export interface AgentStreamEvent {
  type: "thought" | "tool_call" | "tool_result" | "final" | "error";
  content?: string;
  tool?: string;
  args?: Record<string, unknown>;
  records?: unknown[];
  evidence_id?: string;
  answer?: string;
  citations?: string[];
  unsupported_citations?: string[];
  message?: string;
}

/** Accumulated view of an in-progress agentic answer. */
export interface AgentAnswerState {
  trace: AgentTraceStep[];
  answer: string;
  citations: string[];
  unsupportedCitations: string[];
  error?: string;
  done: boolean;
}

export function emptyAgentState(): AgentAnswerState {
  return { trace: [], answer: "", citations: [], unsupportedCitations: [], done: false };
}

/**
 * Pure reducer folding one agent SSE event into the accumulated answer state.
 * Kept pure (no React) so it is unit-testable and reusable.
 */
export function reduceAgentEvent(
  state: AgentAnswerState,
  event: AgentStreamEvent,
): AgentAnswerState {
  switch (event.type) {
    case "thought":
      return { ...state, trace: [...state.trace, { type: "thought", content: event.content }] };
    case "tool_call":
      return {
        ...state,
        trace: [...state.trace, { type: "tool_call", tool: event.tool, args: event.args }],
      };
    case "tool_result":
      return {
        ...state,
        trace: [
          ...state.trace,
          { type: "tool_result", tool: event.tool, recordCount: event.records?.length ?? 0 },
        ],
      };
    case "final":
      return {
        ...state,
        answer: event.answer ?? "",
        citations: event.citations ?? [],
        unsupportedCitations: event.unsupported_citations ?? [],
        done: true,
      };
    case "error":
      return { ...state, error: event.message ?? "Agent error", done: true };
    default:
      return state;
  }
}

/**
 * Stream an agentic (fact-engine) chat response via SSE. Mirrors {@link streamChat}
 * but hits the agent gateway endpoint and yields the agent's trace events.
 */
export async function* streamAgentChat(
  query: string,
  options: {
    chat_history?: Array<{ role: string; content: string }>;
    tags?: string[];
    folder_ids?: string[];
    signal?: AbortSignal;
  } = {},
): AsyncGenerator<AgentStreamEvent> {
  const baseUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";
  const orgId =
    typeof window !== "undefined" ? localStorage.getItem("redarch:currentOrgId") : null;

  const token = await getToken();

  const response = await fetch(`${baseUrl}/search/chat/agent/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(orgId ? { "X-Org-ID": orgId } : {}),
    },
    body: JSON.stringify({
      query,
      chat_history: options.chat_history ?? [],
      tags: options.tags ?? [],
      folder_ids: options.folder_ids ?? [],
    }),
    signal: options.signal,
  });

  if (!response.ok) {
    throw new Error(`Stream failed: ${response.status}`);
  }
  if (!response.body) {
    throw new Error("Stream has no body");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n\n");
      buffer = lines.pop() ?? "";

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed.startsWith("data:")) continue;
        const payload = trimmed.slice(5).trim();
        if (!payload) continue;
        try {
          yield JSON.parse(payload) as AgentStreamEvent;
        } catch {
          // Ignore malformed events rather than tearing down the stream
        }
      }
    }
  } finally {
    try {
      await reader.cancel();
    } catch {
      // Reader may already be released; ignore.
    }
  }
}

/**
 * Stream a RAG chat response via SSE.
 *
 * We use fetch (not EventSource) because EventSource cannot set custom
 * headers for auth or org scoping.
 *
 * Pass `signal` so callers can cancel on unmount / navigation — otherwise
 * the underlying fetch and reader stay open even after the consumer stops
 * iterating, which means brain-api keeps running the LLM call (real cost).
 */
export async function* streamChat(
  query: string,
  options: {
    chat_history?: Array<{ role: string; content: string }>;
    tags?: string[];
    folder_ids?: string[];
    use_knowledge_graph?: boolean;
    signal?: AbortSignal;
  } = {},
): AsyncGenerator<StreamEvent> {
  const baseUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";
  const orgId =
    typeof window !== "undefined" ? localStorage.getItem("redarch:currentOrgId") : null;

  const token = await getToken();

  const response = await fetch(`${baseUrl}/search/chat/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(orgId ? { "X-Org-ID": orgId } : {}),
    },
    body: JSON.stringify({
      query,
      chat_history: options.chat_history ?? [],
      tags: options.tags ?? [],
      folder_ids: options.folder_ids ?? [],
      use_knowledge_graph: options.use_knowledge_graph ?? true,
    }),
    signal: options.signal,
  });

  if (!response.ok) {
    throw new Error(`Stream failed: ${response.status}`);
  }
  if (!response.body) {
    throw new Error("Stream has no body");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n\n");
      buffer = lines.pop() ?? "";

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed.startsWith("data:")) continue;
        const payload = trimmed.slice(5).trim();
        if (!payload) continue;
        try {
          yield JSON.parse(payload) as StreamEvent;
        } catch {
          // Ignore malformed events rather than tearing down the stream
        }
      }
    }
  } finally {
    // Release the underlying network stream even if the consumer throws
    // or aborts. reader.cancel() is idempotent and safe post-done.
    try {
      await reader.cancel();
    } catch {
      // Reader may already be released; ignore.
    }
  }
}
