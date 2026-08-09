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
}

const TITLES: Record<string, string> = {
  assistant: "Said",
  tool_call: "Used a tool",
  tool_result: "Tool replied",
  approval_required: "Asked permission",
  escalation: "Escalated",
};

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
    return { title, body, facts, failed: false };
  }

  if (step.kind === "tool_result") {
    const { body, failed } = resultBody(content.result);
    return { title, body, facts: [], failed };
  }

  // assistant / escalation / anything else: prefer the prose fields the runtime
  // writes, and fall back to nothing rather than dumping the envelope.
  const prose =
    (typeof content.content === "string" && content.content) ||
    (typeof content.reason === "string" && content.reason) ||
    (typeof content.output === "string" && content.output) ||
    null;
  return { title, body: prose, facts: [], failed: step.kind === "error" };
}
