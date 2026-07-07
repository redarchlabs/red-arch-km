/**
 * Retry-policy value logic for a task node's `data.retry`, mirroring the backend
 * authority `services/api/src/api/services/workflow/retry.py` (`read_policy`).
 *
 * A task opts in to retry by carrying a `retry` object; a task without the key
 * fails fast (legacy behaviour). Disabling retry therefore DELETES the key
 * rather than persisting a no-op `max_attempts: 1`. All persisted values are
 * clamped to the same guardrails the engine enforces so a typo can't schedule a
 * runaway or absurdly long retry chain.
 *
 * Pure functions only (no React, no store) so they are trivially unit-testable;
 * the inspector wires them to the designer store's node-data update path.
 */

export interface RetryPolicy {
  /** Total attempts including the first (non-retry) try. `1` = no retry. */
  max_attempts: number;
  /** Base seconds for the exponential back-off between attempts. */
  base_delay_seconds: number;
  /** Ceiling seconds the back-off can never exceed (>= base). */
  max_delay_seconds: number;
}

/** Guardrails — mirror `_MAX_ATTEMPTS_CAP` / `_MAX_DELAY_CAP_SECONDS`. */
export const MAX_ATTEMPTS_CAP = 20;
export const DELAY_CAP_SECONDS = 24 * 3600; // 1 day

/** What "Retry on failure" writes when first enabled (matches read_policy). */
export const DEFAULT_RETRY: RetryPolicy = {
  max_attempts: 3,
  base_delay_seconds: 1,
  max_delay_seconds: 300,
};

function clampInt(value: unknown, fallback: number, low: number, high: number): number {
  const n = Math.floor(Number(value));
  if (!Number.isFinite(n)) return fallback;
  return Math.min(high, Math.max(low, n));
}

function clampNum(value: unknown, fallback: number, low: number, high: number): number {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(high, Math.max(low, n));
}

/**
 * Coerce a loose/partial retry object into a valid, clamped {@link RetryPolicy}.
 * Unlike the strict backend parser this leniently coerces stringy numbers (the
 * `<input type="number">` value is a string) so an edited field lands as a real
 * number in `data.retry`.
 */
export function normalizeRetry(input: unknown): RetryPolicy {
  const spec = input && typeof input === "object" ? (input as Record<string, unknown>) : {};
  const baseDelay = clampNum(spec.base_delay_seconds, DEFAULT_RETRY.base_delay_seconds, 0, DELAY_CAP_SECONDS);
  const maxDelay = clampNum(spec.max_delay_seconds, DEFAULT_RETRY.max_delay_seconds, 0, DELAY_CAP_SECONDS);
  return {
    max_attempts: clampInt(spec.max_attempts, DEFAULT_RETRY.max_attempts, 1, MAX_ATTEMPTS_CAP),
    base_delay_seconds: baseDelay,
    // A retry's back-off can never shrink below its base.
    max_delay_seconds: Math.max(baseDelay, maxDelay),
  };
}

/**
 * The retry policy stored on a node, or `null` when retry is off. An absent,
 * non-object, or empty `retry` all read as off — matching the backend, where an
 * empty dict is treated as no-retry.
 */
export function readRetry(data: Record<string, unknown>): RetryPolicy | null {
  const spec = data.retry;
  if (spec == null || typeof spec !== "object" || Array.isArray(spec)) return null;
  if (Object.keys(spec as Record<string, unknown>).length === 0) return null;
  return normalizeRetry(spec);
}

/**
 * Return a NEW node-data object with the retry policy applied. A non-null policy
 * is normalized and written to `retry`; a `null` policy REMOVES the `retry` key
 * entirely (disabling retry deletes the key, never persists `max_attempts: 1`).
 * The input object is never mutated.
 */
export function applyRetry(
  data: Record<string, unknown>,
  policy: RetryPolicy | null,
): Record<string, unknown> {
  if (policy === null) {
    const next = { ...data };
    delete next.retry;
    return next;
  }
  return { ...data, retry: normalizeRetry(policy) };
}

/**
 * Return a NEW node-data object with the `continue_on_error` flag applied. `true`
 * sets the flag; `false` REMOVES the key so a toggled-then-untoggled task returns
 * to its clean default (absent = do not continue). The input is never mutated.
 */
export function applyContinueOnError(
  data: Record<string, unknown>,
  on: boolean,
): Record<string, unknown> {
  if (!on) {
    const next = { ...data };
    delete next.continue_on_error;
    return next;
  }
  return { ...data, continue_on_error: true };
}
