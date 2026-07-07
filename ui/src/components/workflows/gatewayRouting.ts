/**
 * Routing-mode logic for an exclusive gateway. Its outgoing branches are driven
 * by EITHER a single true/false condition (`data.expr`) or a multi-way list of
 * cases (`data.cases = [{ handle, expr }]`, plus a fallthrough `default` handle).
 * `handlesFor` renders case handles when `cases` is non-empty and true/false
 * handles when only `expr` is set, so the two must stay mutually exclusive.
 *
 * Pure functions only — the inspector uses them to switch modes without leaving
 * a stale `expr`/`cases` behind that would desync the rendered handles.
 */

export type RoutingMode = "condition" | "cases";

/**
 * Which routing mode a gateway's `data` currently expresses. A non-empty
 * `cases` array wins (that's how `handlesFor` decides); otherwise it's the
 * two-way condition mode.
 */
export function routingMode(data: Record<string, unknown>): RoutingMode {
  return Array.isArray(data.cases) && (data.cases as unknown[]).length > 0 ? "cases" : "condition";
}

/**
 * Return NEW data switched to two-way condition routing: the `cases` key is
 * dropped so `handlesFor` falls back to the true/false handles keyed off `expr`.
 * The input is never mutated.
 */
export function toConditionMode(data: Record<string, unknown>): Record<string, unknown> {
  const next = { ...data };
  delete next.cases;
  return next;
}

/**
 * Return NEW data switched to multi-way case routing: `expr` is dropped and
 * `cases` is guaranteed to be an array (existing cases are preserved). The input
 * is never mutated.
 */
export function toCasesMode(data: Record<string, unknown>): Record<string, unknown> {
  const next = { ...data };
  delete next.expr;
  next.cases = Array.isArray(data.cases) ? data.cases : [];
  return next;
}
