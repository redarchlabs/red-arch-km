"use client";

import { ArrowLeft, Send } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { ToolResult } from "@/components/agents/console/ToolResult";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  getAgent,
  streamAgentConsole,
  type Agent,
  type AgentConsoleEvent,
} from "@/lib/api/agents";
import { getApiErrorMessage } from "@/lib/api/errors";

interface ToolBlock {
  kind: "tool";
  name: string;
  args: Record<string, unknown>;
  result?: Record<string, unknown>;
  approval?: boolean;
}
interface TextBlock {
  kind: "user" | "assistant";
  text: string;
}
type Block = TextBlock | ToolBlock;

export default function AgentConsolePage() {
  const { id } = useParams<{ id: string }>();
  const [agent, setAgent] = useState<Agent | null>(null);
  const [blocks, setBlocks] = useState<Block[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    void getAgent(id).then(setAgent).catch(() => setAgent(null));
  }, [id]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [blocks]);

  const applyEvent = useCallback((event: AgentConsoleEvent) => {
    setBlocks((prev) => {
      const next = [...prev];
      if (event.type === "delta") {
        const last = next[next.length - 1];
        if (last && last.kind === "assistant") {
          next[next.length - 1] = { ...last, text: last.text + event.content };
        } else {
          next.push({ kind: "assistant", text: event.content });
        }
      } else if (event.type === "tool_call") {
        next.push({ kind: "tool", name: event.name, args: event.arguments });
      } else if (event.type === "tool_result") {
        for (let i = next.length - 1; i >= 0; i--) {
          const b = next[i];
          if (b.kind === "tool" && b.name === event.name && b.result === undefined) {
            next[i] = { ...b, result: event.result };
            break;
          }
        }
      } else if (event.type === "approval_required") {
        next.push({ kind: "tool", name: event.name, args: event.arguments, approval: true });
      }
      return next;
    });
  }, []);

  const send = async () => {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    setError(null);
    setBusy(true);
    const history = [
      ...blocks
        .filter((b): b is TextBlock => b.kind === "user" || b.kind === "assistant")
        .map((b) => ({ role: b.kind, content: b.text })),
      { role: "user" as const, content: text },
    ];
    setBlocks((prev) => [...prev, { kind: "user", text }]);
    try {
      for await (const event of streamAgentConsole(id, history)) {
        if (event.type === "error") {
          setError(event.error);
        } else {
          applyEvent(event);
        }
      }
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Console stream failed"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex h-[calc(100vh-8rem)] flex-col space-y-4">
      <div className="flex items-center gap-3">
        <Link href="/agents" className="text-muted-foreground hover:text-foreground">
          <ArrowLeft className="h-5 w-5" />
        </Link>
        <div>
          <h1 className="text-xl font-semibold">{agent?.display_name ?? agent?.name ?? "Agent"} console</h1>
          {agent ? (
            <p className="text-xs text-muted-foreground">
              {agent.kind} · {agent.provider} · {agent.model}
            </p>
          ) : null}
        </div>
      </div>

      <Card className="flex-1 overflow-hidden">
        <CardContent ref={scrollRef} className="h-full space-y-3 overflow-y-auto pt-6">
          {blocks.length === 0 ? (
            <p className="text-sm text-muted-foreground">Send a message to start.</p>
          ) : null}
          {blocks.map((b, i) =>
            b.kind === "tool" ? (
              <div key={i} className="rounded-md border bg-muted/40 p-2 text-xs">
                <div className="font-mono font-medium">
                  {b.approval ? "⏸ approval required · " : "🔧 "}
                  {b.name}
                </div>
                <pre className="mt-1 overflow-x-auto whitespace-pre-wrap text-muted-foreground">
                  {JSON.stringify(b.args, null, 2)}
                </pre>
                {b.result ? <ToolResult name={b.name} result={b.result} /> : null}
              </div>
            ) : (
              <div
                key={i}
                className={
                  b.kind === "user"
                    ? "ml-auto max-w-[80%] rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground"
                    : "max-w-[85%] whitespace-pre-wrap text-sm"
                }
              >
                {b.text}
              </div>
            ),
          )}
        </CardContent>
      </Card>

      {error ? <p className="text-sm text-destructive">{error}</p> : null}

      <div className="flex gap-2">
        <Input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void send();
            }
          }}
          placeholder="Ask the agent to do something…"
          disabled={busy}
        />
        <Button onClick={() => void send()} disabled={busy || !input.trim()}>
          <Send className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}
