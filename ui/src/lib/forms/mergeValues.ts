/**
 * Merge helpers for a LIVE view refresh.
 *
 * A view with `config.refresh_ms` re-fetches its render on a cadence, so the screen
 * follows the record as workflows change it. The renderer seeds its value state once
 * at mount, so refreshed values have to be merged in — but a refresh must never
 * overwrite something the viewer is in the middle of editing, and a poll that changed
 * nothing must not produce a new state object (that would re-render the whole tree
 * every few seconds).
 */

/** Structural equality: identical scalars, or deep-equal objects/arrays. A JSON field
 * (or a whole related-record tree) that came back unchanged must not read as a change. */
export function sameValue(a: unknown, b: unknown): boolean {
  if (Object.is(a, b)) return true;
  if (a == null || b == null || typeof a !== "object" || typeof b !== "object") return false;
  try {
    return JSON.stringify(a) === JSON.stringify(b);
  } catch {
    return false; // cyclic / non-serializable: treat as changed
  }
}

/**
 * Adopt freshly-fetched server values over the current ones.
 *
 * - Keys in `dirty` (edited by the viewer) keep their local value.
 * - Keys absent from `server` are left alone — a standalone `input`'s state lives only
 *   in the browser, so a refresh must not wipe it.
 * - Returns the SAME object reference when nothing changed, so React can bail out.
 */
export function mergeServerValues<T extends Record<string, unknown>>(
  current: T,
  server: Record<string, unknown> | null | undefined,
  dirty: ReadonlySet<string>,
): T {
  if (!server) return current;
  let changed = false;
  const next: Record<string, unknown> = { ...current };
  for (const [key, value] of Object.entries(server)) {
    if (dirty.has(key) || sameValue(current[key], value)) continue;
    next[key] = value;
    changed = true;
  }
  return changed ? (next as T) : current;
}
