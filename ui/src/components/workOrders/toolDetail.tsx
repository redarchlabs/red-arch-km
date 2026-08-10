"use client";

/**
 * What a tool call actually did, shown while it is doing it.
 *
 * The live panel used to render a tool call as its name and the word "done" — so a
 * run that had fetched six pages, read two records and rewritten a checklist looked
 * identical to one that had done nothing, and the only account of the work was the
 * agent's own summary at the end. That summary is the *journal*: what the agent
 * chose to say about the work. The live view is supposed to be the other thing —
 * what is happening, in the agent's own arguments, before anyone has narrated it.
 *
 * The arguments and results were already on the wire; the panel was dropping them.
 */

/** Longest single value shown before it is cut. Enough for a page's text excerpt or
 *  a long delegation brief; short of pasting a whole document into the panel. */
const MAX_CHARS = 4_000;

/** Height a payload gets before it scrolls inside itself, so one big result cannot
 *  push every other step off the screen. */
const BOX = "max-h-48";

export function formatPayload(value: unknown): string {
  if (value === undefined || value === null) return "";
  if (typeof value === "string") return value;
  // A tool called with no arguments prints "{}", which is a heading and a pair of
  // braces telling the reader nothing. Empty is empty.
  if (typeof value === "object" && Object.keys(value as object).length === 0)
    return "";
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    // Circular or otherwise unserialisable — better to show its shape than to
    // drop the step's only evidence.
    return String(value);
  }
}

function clamp(text: string): { body: string; cut: number } {
  if (text.length <= MAX_CHARS) return { body: text, cut: 0 };
  return { body: text.slice(0, MAX_CHARS), cut: text.length - MAX_CHARS };
}

function Payload({ label, value }: { label: string; value: unknown }) {
  const text = formatPayload(value);
  if (!text.trim()) return null;
  const { body, cut } = clamp(text);
  return (
    <div className="mt-1">
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <pre
        className={`${BOX} overflow-auto whitespace-pre-wrap break-all rounded bg-muted/60 p-1.5 font-mono text-[11px] leading-snug`}
      >
        {body}
        {cut > 0 ? `\n… ${cut.toLocaleString()} more characters` : ""}
      </pre>
    </div>
  );
}

/** One tool call: its name, whether it has come back, and both payloads. */
export function ToolDetail({
  name,
  args,
  result,
  agent,
  awaitingApproval = false,
}: {
  name: string;
  args: unknown;
  result?: unknown;
  agent?: string | null;
  awaitingApproval?: boolean;
}) {
  const running = result === undefined;
  return (
    <div className="rounded-md border border-dashed p-2 text-xs">
      <div className="flex items-center gap-2">
        <span className="font-mono font-medium">{name}</span>
        {agent ? (
          <span className="text-[10px] text-muted-foreground">{agent}</span>
        ) : null}
        <span
          className={
            running
              ? "text-amber-600 dark:text-amber-400"
              : "text-muted-foreground"
          }
        >
          {awaitingApproval && running
            ? "waiting for your approval"
            : running
              ? "running…"
              : "done"}
        </span>
      </div>
      <Payload label="arguments" value={args} />
      <Payload label="result" value={result} />
    </div>
  );
}
