/**
 * The live agent socket — watch a run think, and say something back.
 *
 * A browser cannot set headers on a WebSocket, so the Clerk bearer token the rest
 * of the API uses is unavailable at connect time. A ticket is minted over an
 * ordinary authenticated request and spent immediately: opaque, single-use, and
 * dead in sixty seconds, so one left in a log or a history entry is worth nothing.
 */
import apiClient from "./client";

export interface LiveTicket {
  ticket: string;
  expires_in: number;
}

/** One frame from a running agent. Mirrors the executor's emit vocabulary. */
export type LiveEvent =
  | { type: "delta"; content: string; run_id: string; agent: string | null }
  | { type: "tool_call"; id?: string; name: string; arguments: unknown; run_id: string; agent: string | null }
  | { type: "tool_result"; name: string; result: unknown; run_id: string; agent: string | null }
  | { type: "approval_required"; name: string; arguments: unknown; run_id: string; agent: string | null }
  | { type: "steer"; content: string; run_id: string; agent: string | null }
  | { type: "usage"; total_tokens: number; run_id: string; agent: string | null }
  | { type: "done"; run_id: string; agent: string | null }
  | { type: "error"; error: string; run_id: string; agent: string | null }
  | { type: "steer_queued"; run_id: string; when: string }
  // A message typed after every run finished: delivered to the work order instead
  // of dropped, which either starts a fresh run or is recorded for whoever does.
  | { type: "steer_restarted"; work_order_id: string }
  | { type: "steer_recorded"; work_order_id: string }
  | { type: "steer_rejected"; run_id?: string; work_order_id?: string; reason: string }
  | { type: "pong" };

export async function mintLiveTicket(): Promise<LiveTicket> {
  return (await apiClient.post<LiveTicket>("/agents/live/ticket", {})).data;
}

/** `http(s)://host/api` -> `ws(s)://host/api/agents/live/ws`. */
export function liveSocketUrl(ticket: string, params: Record<string, string>): string {
  const base = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";
  const url = new URL(`${base.replace(/\/$/, "")}/agents/live/ws`);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.searchParams.set("ticket", ticket);
  for (const [key, value] of Object.entries(params)) {
    if (value) url.searchParams.set(key, value);
  }
  return url.toString();
}
