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

describe("reading the whole record", () => {
  // The runtime compacts what the MODEL re-reads but stores everything. These
  // cover the reader's side of that bargain: a long step is previewed on screen,
  // and the full text is always there to open.
  const long = "L".repeat(3000);

  it("previews a long tool result instead of unrolling it", () => {
    const readable = readableStep(step("tool_result", { result: { output: long } }, "read_file"));

    expect(readable.body!.length).toBeLessThan(long.length);
    expect(readable.truncated).toBe(true);
  });

  it("keeps the full text available to open", () => {
    const readable = readableStep(step("tool_result", { result: { output: long } }, "read_file"));

    expect(readable.detail).toContain(long);
  });

  it("leaves a short result alone and offers nothing to expand", () => {
    const readable = readableStep(step("tool_result", { result: { output: "12 days" } }, "get_record"));

    expect(readable.body).toBe("12 days");
    expect(readable.truncated).toBe(false);
  });

  it("still exposes the raw record for a step whose shape it cannot read", () => {
    // The point of the detail pane: nothing stored is unreachable, even when the
    // readable mapping has nothing to say about it.
    const readable = readableStep(step("tool_result", { result: { rows: [1, 2, 3] } }, "list_records"));

    expect(readable.body).toBeNull();
    expect(readable.detail).toContain("rows");
  });
});

describe("a compaction step", () => {
  const content = {
    summary: "Searched the handbook and found the leave policy.",
    folded: 8,
    before_chars: 64000,
    after_chars: 9000,
  };

  it("reads as what it is, with the summary as the prose", () => {
    const readable = readableStep(step("compaction", content));

    expect(readable.title).toBe("Summarised earlier steps");
    expect(readable.body).toBe("Searched the handbook and found the leave policy.");
  });

  it("says how much history it stands in for", () => {
    // Without this the gap is unexplained — a reader cannot tell a quiet run from
    // one whose middle was folded away.
    const readable = readableStep(step("compaction", content));

    expect(readable.facts).toEqual([
      { label: "messages folded", value: "8" },
      { label: "size", value: "64,000 → 9,000 chars" },
    ]);
  });
});
