/**
 * Deadline maths for the `countdown` element.
 *
 * Pure functions over (element config, record values, "now") so the rules can be
 * tested without mounting a ticking component — the one thing that is genuinely
 * hard to assert against a live clock.
 *
 * A countdown is display-only. Nothing here decides when time is *actually* up:
 * the workflow that opened the question is what closes it. This only draws how
 * long is left, so being a second out is a cosmetic problem, not a correctness
 * one — which is the reason it is safe to run off the viewer's own clock.
 */

export interface CountdownConfig {
  until_field?: string | null;
  from_field?: string | null;
  seconds?: number | null;
  seconds_field?: string | null;
}

export interface CountdownState {
  /** Milliseconds left, clamped to `[0, totalMs]`. Null when nothing resolves — the
   * element renders nothing at all rather than a stuck `0:00` on every record that
   * has no live question. */
  remainingMs: number | null;
  /** The full span the bar depletes across, or null when only an absolute deadline
   * is known (there is then no honest "how far through" to draw). */
  totalMs: number | null;
}

/**
 * Read a timestamp the API sent us.
 *
 * A value with no timezone designator is read as UTC, not as local time. Postgres
 * `timestamptz` values come back with an offset, but a hand-written record field
 * may not, and JS reads a bare `2026-08-03T19:00:00` as the *viewer's* local time —
 * which in Utah is a six-hour countdown instead of a twenty-second one.
 */
export function parseInstant(raw: unknown): number | null {
  if (raw instanceof Date) return Number.isNaN(raw.getTime()) ? null : raw.getTime();
  if (typeof raw === "number") return Number.isFinite(raw) ? raw : null;
  if (typeof raw !== "string") return null;
  const text = raw.trim();
  if (!text) return null;
  const zoned = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(text) ? text : `${text.replace(" ", "T")}Z`;
  const ms = Date.parse(zoned);
  return Number.isNaN(ms) ? null : ms;
}

/** A positive number of seconds from a config value or a record field. */
function readSeconds(el: CountdownConfig, values: Record<string, unknown>): number | null {
  const fromField = el.seconds_field ? values[el.seconds_field] : undefined;
  const raw = fromField ?? el.seconds;
  const n = typeof raw === "string" ? Number(raw.trim()) : typeof raw === "number" ? raw : NaN;
  return Number.isFinite(n) && n > 0 ? n : null;
}

/**
 * Where the clock stands right now.
 *
 * Two ways to say when time runs out, and a record may carry either:
 * an absolute deadline (`until_field`), or a start plus a duration
 * (`from_field` + `seconds`/`seconds_field`). The absolute form wins when both
 * resolve, since it is the more specific statement.
 */
export function countdownState(
  el: CountdownConfig,
  values: Record<string, unknown>,
  nowMs: number,
): CountdownState {
  const seconds = readSeconds(el, values);
  const totalFromDuration = seconds == null ? null : seconds * 1000;

  const until = el.until_field ? parseInstant(values[el.until_field]) : null;
  const start = el.from_field ? parseInstant(values[el.from_field]) : null;

  let deadline: number | null = null;
  let totalMs: number | null = null;
  if (until != null) {
    deadline = until;
    // With only an end time there is no span to draw a bar against — unless a
    // duration was also given, in which case it is the scale.
    totalMs = totalFromDuration;
  } else if (start != null && totalFromDuration != null) {
    deadline = start + totalFromDuration;
    totalMs = totalFromDuration;
  }
  if (deadline == null) return { remainingMs: null, totalMs: null };

  // Clamped at both ends. The floor keeps an expired question at 0:00 instead of
  // counting up into negatives; the ceiling means a viewer whose device clock is
  // badly wrong sees a full bar rather than "4:31:07 left" on a 20-second question.
  const raw = deadline - nowMs;
  const remainingMs = totalMs == null ? Math.max(raw, 0) : Math.min(Math.max(raw, 0), totalMs);
  return { remainingMs, totalMs };
}

/** `0:07` / `1:04` — leading minutes only once there are any, because a
 * twenty-second question reads better as `20` than as `0:20`. */
export function formatRemaining(remainingMs: number): string {
  const secs = Math.ceil(remainingMs / 1000);
  if (secs < 60) return String(secs);
  const mins = Math.floor(secs / 60);
  return `${mins}:${String(secs % 60).padStart(2, "0")}`;
}
