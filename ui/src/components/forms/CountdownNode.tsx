"use client";

import { useEffect, useState } from "react";

import type { CountdownElement } from "@/lib/api/forms";
import { countdownState, formatRemaining } from "@/lib/forms/countdown";

/** How often the clock redraws. Four times a second rather than once, so the
 * number never appears to skip or stall between ticks — and each tick is
 * recomputed from the deadline rather than decremented, so a backgrounded phone
 * that misses a hundred ticks comes back showing the right time, not a stale one. */
const TICK_MS = 250;

/** Below this fraction of the span the clock goes urgent. Two thresholds rather
 * than a gradient: the point is to be readable across a room at a glance. */
const WARN_AT = 0.5;
const URGENT_AT = 0.2;

/**
 * "Time left" — a live clock over a deadline carried on the record.
 *
 * A real component (not one of FormRenderer's inlined node functions) because it
 * owns a timer: it must keep its identity across the parent's re-renders, or the
 * interval is torn down and recreated on every keystroke elsewhere on the page.
 */
export function CountdownNode({
  el,
  values,
}: {
  el: CountdownElement;
  values: Record<string, unknown>;
}) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), TICK_MS);
    return () => window.clearInterval(t);
  }, []);

  const { remainingMs, totalMs } = countdownState(el, values, now);
  // No deadline on the record: nothing is being timed, so draw nothing. A view can
  // therefore leave the countdown in place between questions without it sitting
  // there frozen at zero.
  if (remainingMs == null) return null;

  const done = remainingMs <= 0;
  const fraction = totalMs && totalMs > 0 ? remainingMs / totalMs : 1;
  const tone = done || fraction <= URGENT_AT ? "urgent" : fraction <= WARN_AT ? "warn" : "calm";
  const text: Record<string, string> = {
    calm: "text-foreground",
    warn: "text-warning",
    urgent: "text-destructive",
  };
  const fill: Record<string, string> = {
    calm: "bg-primary",
    warn: "bg-amber-500",
    urgent: "bg-destructive",
  };

  return (
    <div className="flex flex-col items-center gap-2">
      {el.label ? (
        <span className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">
          {el.label}
        </span>
      ) : null}
      <span
        // `tabular-nums` so the digits don't jitter the layout as they change, and
        // aria-live off: a number that changes four times a second would make a
        // screen reader unusable. The deadline is announced by the label instead.
        aria-live="off"
        className={`text-5xl font-black leading-none tabular-nums transition-colors sm:text-6xl ${text[tone]} ${
          tone === "urgent" && !done ? "animate-pulse" : ""
        }`}
      >
        {done ? (el.done_text ?? "Time's up") : formatRemaining(remainingMs)}
      </span>
      {el.show_bar !== false && totalMs ? (
        <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
          <div
            className={`h-full rounded-full transition-[width] duration-200 ease-linear ${fill[tone]}`}
            style={{ width: `${Math.round(fraction * 100)}%` }}
          />
        </div>
      ) : null}
    </div>
  );
}
