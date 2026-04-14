import { getToken, refreshToken } from "@/lib/auth/keycloak";

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

/**
 * Stream a RAG chat response via SSE.
 *
 * We use fetch (not EventSource) because EventSource cannot set custom
 * headers for auth or org scoping.
 */
export async function* streamChat(
  query: string,
  options: {
    chat_history?: Array<{ role: string; content: string }>;
    tags?: string[];
    use_knowledge_graph?: boolean;
  } = {},
): AsyncGenerator<StreamEvent> {
  const baseUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";
  const orgId =
    typeof window !== "undefined" ? localStorage.getItem("redarch:currentOrgId") : null;

  await refreshToken(30);
  const token = getToken();

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
      use_knowledge_graph: options.use_knowledge_graph ?? true,
    }),
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
}
