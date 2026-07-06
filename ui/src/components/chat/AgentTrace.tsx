"use client";

import { ChevronDown, ChevronRight, Search, Wrench, Brain } from "lucide-react";
import { useState } from "react";

import type { AgentTraceStep } from "@/lib/api/search";
import { cn } from "@/lib/utils";

interface AgentTraceProps {
  steps: AgentTraceStep[];
  /** Show the trace expanded while the agent is still working. */
  live?: boolean;
}

function stepLabel(step: AgentTraceStep): string {
  if (step.type === "thought") return step.content ?? "Thinking…";
  if (step.type === "tool_call") {
    const args = step.args ? Object.values(step.args).filter(Boolean).join(", ") : "";
    return args ? `${step.tool}(${args})` : `${step.tool}`;
  }
  return `${step.tool} → ${step.recordCount ?? 0} fact${step.recordCount === 1 ? "" : "s"}`;
}

function StepIcon({ type }: { type: AgentTraceStep["type"] }) {
  const className = "h-3.5 w-3.5 shrink-0";
  if (type === "thought") return <Brain className={cn(className, "text-violet-500")} />;
  if (type === "tool_call") return <Wrench className={cn(className, "text-amber-500")} />;
  return <Search className={cn(className, "text-emerald-500")} />;
}

/**
 * Renders the agent's reasoning trace (thoughts + tool calls + results) as a
 * collapsible timeline beneath an answer, so the multi-step loop reads as
 * transparency rather than latency.
 */
export function AgentTrace({ steps, live = false }: AgentTraceProps) {
  const [open, setOpen] = useState(live);
  if (steps.length === 0) return null;

  return (
    <div className="mt-3 border-t pt-2">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground"
        aria-expanded={open}
      >
        {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        {live ? "Reasoning…" : `Reasoning (${steps.length} steps)`}
      </button>
      {open ? (
        <ol className="mt-2 space-y-1.5 border-l border-muted-foreground/20 pl-3">
          {steps.map((step, i) => (
            <li key={i} className="flex items-start gap-2 text-xs text-muted-foreground">
              <StepIcon type={step.type} />
              <span className="break-words">{stepLabel(step)}</span>
            </li>
          ))}
        </ol>
      ) : null}
    </div>
  );
}
