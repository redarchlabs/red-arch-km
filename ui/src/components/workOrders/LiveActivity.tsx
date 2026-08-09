"use client";

import { Loader2, Send } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { AttachmentChips } from "@/components/common/AttachmentChips";
import { Markdown } from "@/components/common/Markdown";
import { Button } from "@/components/ui/button";
import { liveSocketUrl, mintLiveTicket, type LiveEvent } from "@/lib/api/agentsLive";
import { usePasteAttach } from "@/lib/usePasteAttach";
import { cn } from "@/lib/utils";

/** One thing that happened, as it is shown. Assistant text accumulates into the
 *  block it started, so a streamed answer reads as one paragraph rather than as
 *  a list of fragments. */
type Block =
  | { kind: "assistant"; agent: string | null; text: string }
  | { kind: "tool"; agent: string | null; name: string; args: unknown; result?: unknown }
  | { kind: "steer"; text: string }
  | { kind: "note"; text: string };

/** Reconnect backoff. A page left open through a deploy should come back on its
 *  own, without hammering the server while it is down. */
const RETRY_MS = [1000, 2000, 5000, 10000, 30000];

interface Props {
  workOrderId: string | null;
  runId?: string | null;
  title?: string | null;
  height?: string;
  allowSteer?: boolean;
}

const HEIGHTS: Record<string, string> = {
  sm: "h-64",
  md: "h-96",
  lg: "h-[32rem]",
  fill: "h-[70vh]",
};

export function LiveActivityNode({
  workOrderId,
  runId,
  title,
  height = "md",
  allowSteer = true,
}: Props) {
  const [blocks, setBlocks] = useState<Block[]>([]);
  const [connected, setConnected] = useState(false);
  const [steer, setSteer] = useState("");
  const [lastRunId, setLastRunId] = useState<string | null>(null);
  const paste = usePasteAttach();
  const boxRef = useRef<HTMLDivElement>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const pinned = useRef(true);

  const apply = useCallback((event: LiveEvent) => {
    if (event.type === "pong") return;
    setBlocks((prev) => {
      const next = [...prev];
      const last = next[next.length - 1];
      if (event.type === "delta") {
        // Same agent still talking -> keep appending to its block.
        if (last && last.kind === "assistant" && last.agent === event.agent) {
          next[next.length - 1] = { ...last, text: last.text + event.content };
        } else {
          next.push({ kind: "assistant", agent: event.agent, text: event.content });
        }
      } else if (event.type === "tool_call" || event.type === "approval_required") {
        next.push({ kind: "tool", agent: event.agent, name: event.name, args: event.arguments });
      } else if (event.type === "tool_result") {
        for (let i = next.length - 1; i >= 0; i--) {
          const b = next[i];
          if (b.kind === "tool" && b.name === event.name && b.result === undefined) {
            next[i] = { ...b, result: event.result };
            break;
          }
        }
      } else if (event.type === "steer") {
        next.push({ kind: "steer", text: event.content });
      } else if (event.type === "steer_queued") {
        next.push({ kind: "note", text: "Queued — the agent picks this up on its next turn." });
      } else if (event.type === "steer_rejected") {
        next.push({ kind: "note", text: `Not delivered: ${event.reason}` });
      } else if (event.type === "done") {
        next.push({ kind: "note", text: "Run finished." });
      } else if (event.type === "error") {
        next.push({ kind: "note", text: `Run failed: ${event.error}` });
      }
      return next;
    });
    if ("run_id" in event && event.run_id) setLastRunId(event.run_id);
  }, []);

  useEffect(() => {
    if (!workOrderId && !runId) return;
    let closed = false;
    let attempt = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const connect = async () => {
      if (closed) return;
      try {
        // A fresh ticket per attempt: they are single-use, so a reconnect cannot
        // replay the last one.
        const { ticket } = await mintLiveTicket();
        const socket = new WebSocket(
          liveSocketUrl(ticket, { work_order_id: workOrderId ?? "", run_id: runId ?? "" }),
        );
        socketRef.current = socket;
        socket.onopen = () => {
          attempt = 0;
          setConnected(true);
        };
        socket.onmessage = (message) => {
          try {
            apply(JSON.parse(message.data) as LiveEvent);
          } catch {
            // A frame we cannot parse is skipped rather than shown as an error:
            // the transcript is a view, and one bad frame is not worth breaking it.
          }
        };
        socket.onclose = () => {
          setConnected(false);
          socketRef.current = null;
          if (closed) return;
          timer = setTimeout(() => void connect(), RETRY_MS[Math.min(attempt++, RETRY_MS.length - 1)]);
        };
        socket.onerror = () => socket.close();
      } catch {
        // Minting failed (offline, 403). Back off and try again — a live view that
        // cannot connect must degrade to an empty panel, never to a broken page.
        if (!closed) timer = setTimeout(() => void connect(), RETRY_MS[Math.min(attempt++, RETRY_MS.length - 1)]);
      }
    };

    void connect();
    return () => {
      closed = true;
      if (timer) clearTimeout(timer);
      socketRef.current?.close();
      socketRef.current = null;
    };
  }, [workOrderId, runId, apply]);

  useEffect(() => {
    const box = boxRef.current;
    if (box && pinned.current) box.scrollTop = box.scrollHeight;
  }, [blocks]);

  const send = () => {
    const text = steer.trim();
    const target = runId || lastRunId;
    const socket = socketRef.current;
    // An attachment on its own is a message: "look at this" with a screenshot.
    if ((!text && paste.documentIds.length === 0) || !socket || socket.readyState !== WebSocket.OPEN || !target) {
      return;
    }
    socket.send(JSON.stringify({ type: "steer", run_id: target, text, document_ids: paste.documentIds }));
    setSteer("");
    paste.clear();
  };

  if (!workOrderId && !runId) {
    return <p className="text-sm text-muted-foreground">No work order selected.</p>;
  }

  // Steering needs a run to steer. Before anything has been seen there is no way
  // to know which one, and a box that silently drops what you type is worse than
  // one that says why it is off.
  const target = runId || lastRunId;

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        {title ? <h3 className="text-sm font-medium">{title}</h3> : null}
        <span className="ml-auto flex items-center gap-1.5 text-xs text-muted-foreground">
          <span className={cn("h-2 w-2 rounded-full", connected ? "bg-emerald-500" : "bg-muted-foreground/40")} />
          {connected ? "Live" : "Reconnecting…"}
        </span>
      </div>
      <div
        ref={boxRef}
        onScroll={() => {
          const box = boxRef.current;
          if (box) pinned.current = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
        }}
        className={`space-y-2 overflow-y-auto rounded-md border p-3 ${HEIGHTS[height] ?? HEIGHTS.md}`}
      >
        {blocks.length === 0 ? (
          <p className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-3 w-3 animate-spin" />
            Waiting for an agent to do something…
          </p>
        ) : (
          blocks.map((block, index) => {
            if (block.kind === "assistant") {
              return (
                <div key={index} className="rounded-md border bg-muted/20 p-2">
                  <div className="mb-1 text-[10px] font-medium text-muted-foreground">{block.agent ?? "agent"}</div>
                  {/* Model-authored. Images stripped for the same reason as the
                      diary: one emitted via a poisoned document would make the
                      reader's browser fetch an attacker's URL. */}
                  <Markdown content={block.text} stripImages className="text-sm" />
                </div>
              );
            }
            if (block.kind === "tool") {
              return (
                <div key={index} className="rounded-md border border-dashed p-2 text-xs">
                  <span className="font-medium">{block.name}</span>
                  <span className="ml-2 text-muted-foreground">
                    {block.result === undefined ? "running…" : "done"}
                  </span>
                </div>
              );
            }
            if (block.kind === "steer") {
              return (
                <div key={index} className="rounded-md border bg-sky-50/60 p-2 text-sm dark:bg-sky-950/30">
                  <div className="mb-1 text-[10px] font-medium text-muted-foreground">you</div>
                  {block.text}
                </div>
              );
            }
            return (
              <p key={index} className="text-xs italic text-muted-foreground">
                {block.text}
              </p>
            );
          })
        )}
      </div>
      {allowSteer ? (
        <div className="space-y-1" onDrop={paste.onDrop} onDragOver={(e) => e.preventDefault()}>
          <AttachmentChips attachments={paste.attachments} onRemove={paste.remove} />
          <div className="flex items-end gap-2">
          <textarea
            value={steer}
            onChange={(e) => setSteer(e.target.value)}
            onKeyDown={(e) => {
              if (e.key !== "Enter" || e.shiftKey) return;
              // preventDefault twice over: this sits inside the renderer's form.
              e.preventDefault();
              send();
            }}
            onPaste={paste.onPaste}
            placeholder={target ? "Say something — paste a screenshot to attach it…" : "Nothing running to steer yet"}
            aria-label="Steer the agent"
            rows={2}
            disabled={!target}
            className="w-full rounded-md border bg-background px-2 py-1.5 text-sm disabled:opacity-60"
          />
          <Button
            type="button"
            size="sm"
            onClick={send}
            disabled={!target || !connected || paste.busy || (!steer.trim() && paste.documentIds.length === 0)}
          >
            <Send className="h-3 w-3" />
          </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
