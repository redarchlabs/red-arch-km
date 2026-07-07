/**
 * Value logic for a `script` task's `data.transform` — a flat map of
 * `{ <variable>: <JsonLogic-expression-or-literal> }` the engine evaluates to
 * set run variables. The inspector edits it as a list of `{ variable, expr }`
 * rows where the expression cell holds JSON text.
 *
 * The text<->value contract is round-trip safe: an expression is parsed as JSON
 * (so `{ "var": "after.x" }`, `42`, `true` become real objects/numbers/booleans)
 * and any text that isn't valid JSON is kept as a plain string literal. A string
 * whose *content* is itself valid JSON (e.g. the literal `"42"`) is JSON-encoded
 * so it never silently re-parses into a number.
 *
 * Pure functions only (no React, no store) so they are trivially unit-testable.
 */

export interface TransformRow {
  /** The variable name the expression is assigned to. */
  key: string;
  /** The expression as JSON text (see module note for the parse contract). */
  expr: string;
}

/** Parse an expression cell's text into its stored value. */
export function parseExprText(text: string): unknown {
  const trimmed = text.trim();
  if (trimmed === "") return "";
  try {
    return JSON.parse(trimmed);
  } catch {
    // Not valid JSON — treat the raw text as a string literal.
    return text;
  }
}

/** Render a stored value back to the expression cell's text form. */
export function exprToText(value: unknown): string {
  if (value === undefined) return "";
  if (typeof value === "string") {
    // A string that is *itself* parseable JSON (`"42"`, `"true"`, `"[1]"`, or a
    // quoted string) is ambiguous — encode it so it round-trips as a string.
    let ambiguous = false;
    try {
      JSON.parse(value);
      ambiguous = true;
    } catch {
      ambiguous = false;
    }
    return ambiguous ? JSON.stringify(value) : value;
  }
  return JSON.stringify(value);
}

function asRecord(value: unknown): Record<string, unknown> {
  return value != null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

/** Read a node's `data.transform` as a plain object (absent/malformed → {}). */
export function readTransform(data: Record<string, unknown>): Record<string, unknown> {
  return asRecord(data.transform);
}

/** Decompose a transform map into editor rows (preserving key order). */
export function transformToRows(value: unknown): TransformRow[] {
  return Object.entries(asRecord(value)).map(([key, v]) => ({ key, expr: exprToText(v) }));
}

/**
 * Build a transform map from editor rows. Blank-keyed rows are dropped; a later
 * row wins a duplicate key (matching plain object semantics). Never mutates.
 */
export function rowsToTransform(rows: TransformRow[]): Record<string, unknown> {
  const obj: Record<string, unknown> = {};
  for (const row of rows) {
    const key = row.key.trim();
    if (!key) continue;
    obj[key] = parseExprText(row.expr);
  }
  return obj;
}
