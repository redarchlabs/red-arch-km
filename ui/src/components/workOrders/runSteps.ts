/** Turn an agent run step into something a person can read.
 *
 * Steps are stored as the shapes the runtime emits — `{content}`, `{arguments}`,
 * `{result}` — which is right for machines and unreadable on screen: a JSON dump
 * with escaped newlines, where the one sentence that matters is buried mid-line.
 *
 * Kept out of the component and dependency-free so the mapping can be asserted
 * directly, and so a new step kind is a change in one place.
 */

export interface ReadableStep {
  /** What happened, in words. */
  title: string;
  /** The prose worth reading, already unescaped. Markdown where the model wrote it. */
  body: string | null;
  /** Short labelled facts (a tool's arguments), rather than a JSON blob. */
  facts: { label: string; value: string }[];
  /** Something went wrong here — the caller tints it. */
  failed: boolean;
  /** Whether ``body`` is a preview of something longer. */
  truncated: boolean;
  /** Everything the step stored, for the reader who asks for it. */
  detail: string;
}

const TITLES: Record<string, string> = {
  assistant: "Said",
  tool_call: "Used a tool",
  tool_result: "Tool replied",
  approval_required: "Asked permission",
  escalation: "Escalated",
  compaction: "Summarised earlier steps",
};

/** On-screen preview length for a step's prose. Generous — the panel scrolls —
 *  but a multi-thousand-character file read should not bury the steps after it. */
const BODY_PREVIEW = 1200;

/** The stored step, pretty-printed. This is the reader's equivalent of the
 *  agent's ``read_run_detail``: the runtime compacts what the MODEL re-reads and
 *  keeps the whole result on the step, so nothing recorded should be unreachable
 *  on screen — including the shapes the readable mapping has no words for. */
function fullDetail(content: Record<string, unknown>): string {
  const payload = "result" in content ? content.result : content;
  if (typeof payload === "string") return payload;
  return JSON.stringify(payload, null, 2);
}

/** Turn a stored value into one readable line. Long text stays whole — the panel
 *  scrolls — but structures collapse rather than sprawling over a screen. */
function asText(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return value.map(asText).filter(Boolean).join(", ");
  return JSON.stringify(value);
}

/** Long enough, or multi-line, to be something a person reads rather than a
 *  parameter they scan. Below this an argument is an identifier or a flag, and a
 *  labelled line is the clearer shape. */
function isProse(value: string): boolean {
  return value.length > 120 || value.includes("\n");
}

function truncate(value: string): string {
  return value.length > 200 ? `${value.slice(0, 200)}…` : value;
}

/** The one field in a tool result actually worth reading. A knowledge search
 *  returns an answer plus its plumbing; showing the plumbing first buries it. */
function resultBody(result: unknown): { body: string | null; failed: boolean } {
  if (typeof result === "string") return { body: result, failed: false };
  if (!result || typeof result !== "object") return { body: null, failed: false };
  const record = result as Record<string, unknown>;
  for (const key of ["error", "answer", "result", "output", "text", "content"]) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) {
      return { body: value, failed: key === "error" };
    }
  }
  return { body: null, failed: false };
}

export function readableStep(step: {
  kind: string;
  name: string | null;
  content: Record<string, unknown> | null;
}): ReadableStep {
  const content = step.content ?? {};
  const base = TITLES[step.kind] ?? step.kind;
  const title = step.name ? `${base}: ${step.name}` : base;
  const detail = fullDetail(content);

  /** Shared tail: preview a long body, and always carry the full record. */
  const finish = (
    body: string | null,
    facts: { label: string; value: string }[],
    failed: boolean,
  ): ReadableStep => {
    const long = body !== null && body.length > BODY_PREVIEW;
    return {
      title,
      body: long ? `${body!.slice(0, BODY_PREVIEW)}…` : body,
      facts,
      failed,
      truncated: long,
      detail,
    };
  };

  if (step.kind === "compaction") {
    // The one step that exists to explain a gap. Its summary IS the prose; the
    // numbers say how much history it stands in for, so a reader can tell a quiet
    // run from one whose middle was folded away.
    const facts: { label: string; value: string }[] = [];
    if (typeof content.folded === "number") {
      facts.push({ label: "messages folded", value: String(content.folded) });
    }
    if (typeof content.before_chars === "number" && typeof content.after_chars === "number") {
      facts.push({
        label: "size",
        value: `${content.before_chars.toLocaleString("en-US")} → ${content.after_chars.toLocaleString("en-US")} chars`,
      });
    }
    return finish(typeof content.summary === "string" ? content.summary : null, facts, false);
  }

  if (step.kind === "tool_call" || step.kind === "approval_required") {
    const args = (content.arguments ?? {}) as Record<string, unknown>;
    const facts: { label: string; value: string }[] = [];
    let body: string | null = null;
    for (const [label, value] of Object.entries(args)) {
      // An argument can BE the work: `reply_to_peer`'s answer, `ask_human`'s
      // question, a delegated task's brief. Those arrive as Markdown, written to
      // be read, and squeezing them into a one-line fact shows the asterisks
      // instead of the formatting. The longest prose argument becomes the body;
      // short scalars stay as labelled facts.
      if (typeof value === "string" && isProse(value) && (body === null || value.length > body.length)) {
        if (body !== null) facts.push({ label, value: truncate(body) });
        body = value;
        continue;
      }
      facts.push({ label, value: asText(value) });
    }
    return finish(body, facts, false);
  }

  if (step.kind === "tool_result") {
    const { body, failed } = resultBody(content.result);
    return finish(body, [], failed);
  }

  // assistant / escalation / anything else: prefer the prose fields the runtime
  // writes, and fall back to nothing rather than dumping the envelope.
  const prose =
    (typeof content.content === "string" && content.content) ||
    (typeof content.reason === "string" && content.reason) ||
    (typeof content.output === "string" && content.output) ||
    null;
  return finish(prose, [], step.kind === "error");
}
