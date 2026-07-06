"use client";

import { Bot, CheckCircle2, Send, Sparkles, Wrench, XCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ACTION_LABELS } from "@/components/workflows/actionTypes";
import { streamConfigAgent, type AgentEvent } from "@/lib/api/agent";

type Block =
  | { kind: "user"; text: string }
  | { kind: "assistant"; text: string }
  | { kind: "tool"; name: string; args: Record<string, unknown>; result?: Record<string, unknown> };

const TOOL_LABELS: Record<string, string> = {
  create_entity: "Create entity",
  add_entity_field: "Add field",
  create_relationship: "Create relationship",
  create_record: "Create record",
  list_entities: "List entities",
  get_entity_schema: "Inspect entity",
  create_workflow: "Create workflow",
  list_workflows: "List workflows",
  search_knowledge_base: "Search documents",
  ...ACTION_LABELS,
};

const SUGGESTIONS = [
  "Create a customer entity with name, email, and status",
  "Add a phone field to the customer entity",
  "Create a workflow on the customer entity",
];

export function AssistantPanel() {
  const [blocks, setBlocks] = useState<Block[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [blocks]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const send = async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || streaming) return;
    setError(null);
    setInput("");

    const history = blocks
      .filter((b): b is Extract<Block, { kind: "user" | "assistant" }> => b.kind !== "tool")
      .map((b) => ({ role: b.kind as "user" | "assistant", content: b.text }));

    setBlocks((prev) => [...prev, { kind: "user", text: trimmed }, { kind: "assistant", text: "" }]);
    setStreaming(true);
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      for await (const event of streamConfigAgent(
        [...history, { role: "user", content: trimmed }],
        { signal: controller.signal },
      )) {
        applyEvent(setBlocks, event, setError);
      }
    } catch (e: unknown) {
      if (!controller.signal.aborted) {
        setError(e instanceof Error ? e.message : "Assistant failed");
      }
    } finally {
      setStreaming(false);
    }
  };

  return (
    <>
      <div ref={scrollRef} className="flex-1 space-y-3 overflow-y-auto rounded-lg border bg-muted/20 p-4">
        {blocks.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-3 text-muted-foreground">
            <Sparkles className="h-8 w-8" />
            <p className="text-sm">
              Describe what you want to build. I can create entities, fields, relationships, records,
              and workflows for you.
            </p>
            <div className="flex flex-wrap justify-center gap-2">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => void send(s)}
                  className="rounded-full border bg-background px-3 py-1 text-xs hover:bg-muted"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        ) : (
          blocks.map((block, i) => <BlockView key={i} block={block} />)
        )}
      </div>

      {error ? (
        <p className="mt-2 text-sm text-destructive" role="alert">
          {error}
        </p>
      ) : null}

      <div className="mt-4 flex gap-2">
        <Textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void send(input);
            }
          }}
          placeholder="e.g. Create a patient entity with name and date of birth…"
          className="min-h-[60px] resize-none"
          disabled={streaming}
        />
        <Button
          onClick={() => void send(input)}
          disabled={streaming || !input.trim()}
          size="icon"
          className="h-auto"
          aria-label="Send"
        >
          <Send className="h-4 w-4" />
        </Button>
      </div>
    </>
  );
}

function applyEvent(
  setBlocks: React.Dispatch<React.SetStateAction<Block[]>>,
  event: AgentEvent,
  setError: (e: string | null) => void,
) {
  if (event.type === "delta") {
    setBlocks((prev) => appendAssistant(prev, event.content));
  } else if (event.type === "tool_call") {
    setBlocks((prev) => {
      // Insert the tool card before the trailing empty assistant block.
      const tool: Block = { kind: "tool", name: event.name, args: event.arguments };
      const idx = prev.length - 1;
      if (prev[idx]?.kind === "assistant") {
        return [...prev.slice(0, idx), tool, prev[idx]];
      }
      return [...prev, tool];
    });
  } else if (event.type === "tool_result") {
    setBlocks((prev) => {
      const next = [...prev];
      for (let i = next.length - 1; i >= 0; i--) {
        const b = next[i];
        if (b.kind === "tool" && b.name === event.name && !b.result) {
          next[i] = { ...b, result: event.result };
          break;
        }
      }
      return next;
    });
  } else if (event.type === "error") {
    setError(event.error);
  }
}

function appendAssistant(blocks: Block[], text: string): Block[] {
  const idx = blocks.length - 1;
  if (blocks[idx]?.kind === "assistant") {
    const b = blocks[idx] as Extract<Block, { kind: "assistant" }>;
    return [...blocks.slice(0, idx), { kind: "assistant", text: b.text + text }];
  }
  return [...blocks, { kind: "assistant", text }];
}

function BlockView({ block }: { block: Block }) {
  if (block.kind === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground">
          {block.text}
        </div>
      </div>
    );
  }
  if (block.kind === "assistant") {
    if (!block.text) return null;
    return (
      <div className="flex gap-2">
        <Bot className="mt-1 h-4 w-4 shrink-0 text-muted-foreground" />
        <div className="max-w-[80%] whitespace-pre-wrap rounded-lg bg-background px-3 py-2 text-sm shadow-sm">
          {block.text}
        </div>
      </div>
    );
  }
  return <ToolCard block={block} />;
}

function ToolCard({ block }: { block: Extract<Block, { kind: "tool" }> }) {
  const label = TOOL_LABELS[block.name] ?? block.name;
  const error = block.result && typeof block.result.error === "string" ? block.result.error : null;
  const done = block.result !== undefined;
  return (
    <div className="ml-6 rounded-md border bg-card px-3 py-2 text-xs">
      <div className="flex items-center gap-2">
        {!done ? (
          <Wrench className="h-3.5 w-3.5 animate-pulse text-muted-foreground" />
        ) : error ? (
          <XCircle className="h-3.5 w-3.5 text-rose-500" />
        ) : (
          <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
        )}
        <span className="font-medium">{label}</span>
        {!done ? <span className="text-muted-foreground">running…</span> : null}
      </div>
      {error ? (
        <p className="mt-1 text-destructive">{error}</p>
      ) : done ? (
        <pre className="mt-1 overflow-x-auto text-[11px] text-muted-foreground">
          {JSON.stringify(block.result, null, 2)}
        </pre>
      ) : null}
    </div>
  );
}
