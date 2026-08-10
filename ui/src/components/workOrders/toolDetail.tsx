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
 * The payloads were already on the wire; the panel was dropping them. How they are
 * laid out lives in payloadView.tsx.
 */

import { PayloadView } from "./payloadView";

export { formatPayload } from "./payloadView";

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
      <PayloadView label="arguments" value={args} />
      <PayloadView label="result" value={result} />
    </div>
  );
}
