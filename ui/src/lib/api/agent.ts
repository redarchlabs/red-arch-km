import { getToken } from "@/lib/auth/clerk";

/** Events emitted by the in-API configuration agent over SSE. */
export type AgentEvent =
  | { type: "delta"; content: string }
  | { type: "tool_call"; name: string; arguments: Record<string, unknown> }
  | { type: "tool_result"; name: string; result: Record<string, unknown> }
  | { type: "done" }
  | { type: "error"; error: string };

export interface AgentChatMessage {
  role: "user" | "assistant";
  content: string;
}

/**
 * Stream the configuration agent. Yields tool_call / tool_result / delta events
 * as the agent inspects and edits the workspace. Mirrors the fetch+SSE reader in
 * search.ts (EventSource can't send auth/org headers).
 */
export async function* streamConfigAgent(
  messages: AgentChatMessage[],
  options: { signal?: AbortSignal } = {},
): AsyncGenerator<AgentEvent> {
  const baseUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";
  const orgId =
    typeof window !== "undefined" ? localStorage.getItem("redarch:currentOrgId") : null;
  const token = await getToken();

  const response = await fetch(`${baseUrl}/agent/chat/stream`, {
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
    throw new Error(`Assistant request failed: ${response.status}`);
  }
  if (!response.body) {
    throw new Error("Assistant stream has no body");
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
          yield JSON.parse(payload) as AgentEvent;
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
