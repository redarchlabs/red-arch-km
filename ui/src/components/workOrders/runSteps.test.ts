import { describe, expect, it } from "vitest";

import { readableStep } from "./runSteps";

const step = (kind: string, content: Record<string, unknown>, name: string | null = null) => ({
  kind,
  name,
  content,
});

describe("readableStep", () => {
  it("shows a tool's arguments as labelled facts, not a JSON blob", () => {
    // A stringified object puts the one thing worth reading — the task — behind
    // braces, quotes and escapes.
    const readable = readableStep(
      step("tool_call", { arguments: { agent: "research-analyst", task: "Audit the site" } }, "delegate_task"),
    );

    expect(readable.title).toBe("Used a tool: delegate_task");
    expect(readable.facts).toEqual([
      { label: "agent", value: "research-analyst" },
      { label: "task", value: "Audit the site" },
    ]);
  });

  it("surfaces the answer from a tool result, not its plumbing", () => {
    // A knowledge search returns an answer plus sources, tokens and keys. Leading
    // with the envelope buries the sentence the reader wants.
    const readable = readableStep(
      step("tool_result", { result: { answer: "The site lacks meta descriptions.", sources: [1, 2] } }),
    );

    expect(readable.body).toBe("The site lacks meta descriptions.");
    expect(readable.failed).toBe(false);
  });

  it("marks a failed tool result and shows the error itself", () => {
    const readable = readableStep(step("tool_result", { result: { error: "knowledge search failed: 500" } }));

    expect(readable.body).toBe("knowledge search failed: 500");
    expect(readable.failed).toBe(true);
  });

  it("prefers an error over an answer when both are present", () => {
    // A partial result alongside an error is still a failure; reporting the
    // answer would present it as success.
    const readable = readableStep(step("tool_result", { result: { answer: "partial", error: "timed out" } }));

    expect(readable.body).toBe("timed out");
    expect(readable.failed).toBe(true);
  });

  it("reads assistant prose straight through, for Markdown rendering", () => {
    const readable = readableStep(step("assistant", { content: "**Audit** first, then fix." }));

    expect(readable.title).toBe("Said");
    expect(readable.body).toBe("**Audit** first, then fix.");
  });

  it("names an escalation's reason", () => {
    const readable = readableStep(step("escalation", { reason: "No access to Ahrefs." }));

    expect(readable.body).toBe("No access to Ahrefs.");
  });

  it("shows nothing rather than dumping an envelope it does not understand", () => {
    // An unrecognised shape should be quiet. Printing the raw object is what made
    // the panel unreadable in the first place.
    const readable = readableStep(step("assistant", { completed: true, output: null }));

    expect(readable.body).toBeNull();
    expect(readable.facts).toEqual([]);
  });

  it("renders a prose argument as the body, so its Markdown formats", () => {
    // `reply_to_peer` carries the whole answer as an argument. As a one-line fact
    // it showed literal asterisks instead of a formatted list.
    const answer = `When the knowledge base search fails, follow these:\n\n1. **Manual Content Audit**: review the content.\n2. **Technical SEO Check**: use Search Console.`;
    const readable = readableStep(step("tool_call", { arguments: { answer } }, "reply_to_peer"));

    expect(readable.body).toBe(answer);
    expect(readable.facts).toEqual([]);
  });

  it("keeps short arguments as labelled facts alongside prose", () => {
    // An agent name is an identifier to scan, not something to read; only the
    // brief is prose.
    const task = "Conduct a manual content audit on redarchlabs.com, evaluating keyword relevance, meta tags, headers and internal linking throughout.";
    const readable = readableStep(
      step("tool_call", { arguments: { agent: "research-analyst", task } }, "delegate_task"),
    );

    expect(readable.body).toBe(task);
    expect(readable.facts).toEqual([{ label: "agent", value: "research-analyst" }]);
  });

  it("flattens a structured argument to one line", () => {
    const readable = readableStep(step("tool_call", { arguments: { tags: ["seo", "audit"] } }, "search"));

    expect(readable.facts).toEqual([{ label: "tags", value: "seo, audit" }]);
  });

  it("falls back to the raw kind for a step type it has no words for", () => {
    // A new runtime step kind should still render, labelled by its own name.
    const readable = readableStep(step("handoff", {}));

    expect(readable.title).toBe("handoff");
  });
});
