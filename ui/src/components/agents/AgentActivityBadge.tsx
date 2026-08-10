"use client";

import { Loader2 } from "lucide-react";

import type { AgentActivity } from "@/lib/api/agents";

/**
 * What this agent is doing, on its roster card.
 *
 * Two states, deliberately styled apart rather than as two neutral chips:
 * **working** is informational and stays quiet; **needs you** is a call to action and
 * borrows the header bell's amber, so the same colour means the same thing wherever
 * you meet it. An idle agent renders nothing — a row of "idle" badges is noise that
 * makes the two that matter harder to find.
 */
export function AgentActivityBadge({
  activity,
  onAnswer,
}: {
  activity: AgentActivity | undefined;
  /** Opens the answer panel. Given only when "needs you" is actionable — the badge
   *  falls back to plain text without it rather than offering a dead button. */
  onAnswer?: () => void;
}) {
  if (!activity) return null;

  if (activity.state === "needs_you") {
    const n = activity.waiting_on_you;
    const label = `Needs you${n > 1 ? ` (${n})` : ""}`;
    const skin =
      "inline-flex items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-900 dark:bg-amber-900/40 dark:text-amber-100";
    const title =
      n === 1
        ? "One thing is waiting on your answer"
        : `${n} things are waiting on your answer`;

    // Clickable, because the badge already told you which agent is stuck; making you
    // leave for a shared inbox to act on it is where "I'll do it later" comes from.
    return onAnswer ? (
      <button
        type="button"
        onClick={onAnswer}
        title={`${title} — click to answer`}
        className={`${skin} hover:bg-amber-200 dark:hover:bg-amber-900/60`}
      >
        {label}
      </button>
    ) : (
      <span title={title} className={skin}>
        {label}
      </span>
    );
  }

  const runs = activity.live_runs;
  return (
    <span
      title={runs === 1 ? "One run in progress" : `${runs} runs in progress`}
      className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-900 dark:bg-emerald-900/40 dark:text-emerald-100"
    >
      {/* The spin is the point: it distinguishes "started something a second ago"
          from "shows a static label because nobody refreshed the page". */}
      <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
      Working{runs > 1 ? ` (${runs})` : ""}
    </span>
  );
}
