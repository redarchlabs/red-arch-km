import { getToken } from "@/lib/auth/clerk";

import type { RunSnapshot } from "@/components/workflows/runOverlay";

/** A parsed Server-Sent Events frame (`event:` + one-or-more `data:` lines). */
export interface SseFrame {
  event: string;
  data: string;
}

/** Parse one SSE frame (text between blank lines). Returns null if it carries no
 * data. Pure + unit-tested. */
export function parseSseFrame(frame: string): SseFrame | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (dataLines.length === 0) return null;
  return { event, data: dataLines.join("\n") };
}

export type RunStreamEvent =
  | { type: "snapshot"; snapshot: RunSnapshot }
  | { type: "done" }
  | { type: "error"; detail: string };

/**
 * Consume the run-state SSE stream (GET /workflows/runs/{runId}/stream). Yields a
 * `snapshot` per state change, then `done` when the run finishes. Mirrors the
 * fetch+reader SSE pattern in agent.ts (EventSource can't send auth/org headers).
 * The caller falls back to polling if this throws / the stream is unavailable.
 */
export async function* streamRun(
  runId: string,
  options: { signal?: AbortSignal } = {},
): AsyncGenerator<RunStreamEvent> {
  const baseUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";
  const orgId = typeof window !== "undefined" ? localStorage.getItem("redarch:currentOrgId") : null;
  const token = await getToken();

  const response = await fetch(`${baseUrl}/workflows/runs/${runId}/stream`, {
    method: "GET",
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(orgId ? { "X-Org-ID": orgId } : {}),
    },
    signal: options.signal,
  });
  if (!response.ok || !response.body) {
    throw new Error(`Run stream failed: ${response.status}`);
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
      for (const raw of frames) {
        const frame = parseSseFrame(raw);
        if (!frame) continue;
        if (frame.event === "snapshot") {
          try {
            yield { type: "snapshot", snapshot: JSON.parse(frame.data) as RunSnapshot };
          } catch {
            // Skip a malformed frame rather than tearing down the stream.
          }
        } else if (frame.event === "done") {
          yield { type: "done" };
          return;
        } else if (frame.event === "error") {
          yield { type: "error", detail: frame.data };
          return;
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
