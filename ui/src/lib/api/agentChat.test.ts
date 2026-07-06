import { describe, expect, it } from "vitest";

import {
  emptyAgentState,
  reduceAgentEvent,
  type AgentStreamEvent,
} from "./search";

function fold(events: AgentStreamEvent[]) {
  return events.reduce(reduceAgentEvent, emptyAgentState());
}

describe("reduceAgentEvent", () => {
  it("appends thoughts and tool calls to the trace in order", () => {
    const state = fold([
      { type: "thought", content: "look up HQ" },
      { type: "tool_call", tool: "claim_query", args: { subject: "Acme" } },
    ]);
    expect(state.trace).toHaveLength(2);
    expect(state.trace[0]).toEqual({ type: "thought", content: "look up HQ" });
    expect(state.trace[1]).toMatchObject({ type: "tool_call", tool: "claim_query" });
  });

  it("records fact counts on tool results", () => {
    const state = fold([
      { type: "tool_result", tool: "claim_query", records: [{}, {}, {}] },
    ]);
    expect(state.trace[0]).toEqual({ type: "tool_result", tool: "claim_query", recordCount: 3 });
  });

  it("captures the final answer, citations, and grounding on `final`", () => {
    const state = fold([
      { type: "thought", content: "answer" },
      {
        type: "final",
        answer: "Acme is in Paris [E1].",
        citations: ["E1"],
        unsupported_citations: ["E9"],
      },
    ]);
    expect(state.answer).toBe("Acme is in Paris [E1].");
    expect(state.citations).toEqual(["E1"]);
    expect(state.unsupportedCitations).toEqual(["E9"]);
    expect(state.done).toBe(true);
  });

  it("surfaces errors and marks the stream done", () => {
    const state = fold([{ type: "error", message: "boom" }]);
    expect(state.error).toBe("boom");
    expect(state.done).toBe(true);
  });

  it("does not mutate the previous state (immutability)", () => {
    const start = emptyAgentState();
    const next = reduceAgentEvent(start, { type: "thought", content: "x" });
    expect(start.trace).toHaveLength(0);
    expect(next.trace).toHaveLength(1);
  });
});
