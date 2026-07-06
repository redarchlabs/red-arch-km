/** Helpers to convert between a friendly rule-row editor and JsonLogic. */

export const CONDITION_OPS = ["==", "!=", ">", ">=", "<", "<=", "in"] as const;
export type ConditionOp = (typeof CONDITION_OPS)[number];

export interface ConditionRow {
  field: string; // a var path, e.g. "after.status"
  op: ConditionOp;
  value: string;
}

export type JsonLogic = Record<string, unknown> | null;

/**
 * Coerce an editor string into a JsonLogic scalar.
 *
 * A raw `Number()` cast silently corrupts string fields whose contents merely
 * look numeric ("01234" → 1234, "1e3" → 1000, phone/order codes). We only turn
 * the value into a number when the field is *declared* numeric, or when the
 * value round-trips exactly (`String(n) === trimmed`) so no information is lost.
 */
function coerce(value: string, numeric = false): unknown {
  const trimmed = value.trim();
  if (trimmed === "") return "";
  if (trimmed === "true") return true;
  if (trimmed === "false") return false;
  const n = Number(trimmed);
  if (Number.isNaN(n)) return trimmed;
  return numeric || String(n) === trimmed ? n : trimmed;
}

/** Whether a JsonLogic argument is a comparable scalar (not a var/list/object). */
function isScalar(x: unknown): boolean {
  return x === null || ["string", "number", "boolean"].includes(typeof x);
}

/** Render a scalar back to the editor's string form. */
function scalarToText(x: unknown): string {
  if (typeof x === "boolean") return x ? "true" : "false";
  if (x === null) return "";
  return String(x);
}

/** Resolves whether a var path targets a numeric entity field. */
export type NumericResolver = (fieldPath: string) => boolean;

function compileRow(row: ConditionRow, numeric = false): JsonLogic {
  const varRef = { var: row.field };
  if (row.op === "in") {
    // Trim + drop blanks so "" / "  " yield [] (incomplete), not [""].
    const list = row.value
      .split(",")
      .map((v) => v.trim())
      .filter((v) => v !== "")
      .map((v) => coerce(v, numeric));
    return { in: [varRef, list] };
  }
  return { [row.op]: [varRef, coerce(row.value, numeric)] };
}

/**
 * Compile AND-combined rows to a JsonLogic expression (null = always true).
 * `isNumeric` (optional) lets callers thread entity field types through so a
 * numeric field coerces "01234" to a number while a text field keeps the string.
 */
export function compileRows(rows: ConditionRow[], isNumeric?: NumericResolver): JsonLogic {
  const numeric = (r: ConditionRow) => isNumeric?.(r.field) ?? false;
  const valid = rows.filter((r) => r.field.trim() !== "");
  if (valid.length === 0) return null;
  if (valid.length === 1) return compileRow(valid[0], numeric(valid[0]));
  return { and: valid.map((r) => compileRow(r, numeric(r))) };
}

function parseClause(clause: unknown): ConditionRow | null {
  if (!clause || typeof clause !== "object") return null;
  const entries = Object.entries(clause as Record<string, unknown>);
  if (entries.length !== 1) return null;
  const [op, args] = entries[0];
  if (op === "in" && Array.isArray(args) && args.length === 2) {
    const [needle, haystack] = args;
    // Only scalar lists round-trip through the comma-joined editor.
    if (isVar(needle) && Array.isArray(haystack) && haystack.every(isScalar)) {
      const parts = haystack.map(scalarToText);
      // A comma inside an element can't survive a comma-join/split — keep raw.
      if (parts.some((p) => p.includes(","))) return null;
      return { field: needle.var, op: "in", value: parts.join(", ") };
    }
    return null;
  }
  if ((CONDITION_OPS as readonly string[]).includes(op) && Array.isArray(args) && args.length === 2) {
    const [left, right] = args;
    // Right side must be a scalar; a var/list/object can't be shown as a row.
    if (isVar(left) && isScalar(right)) {
      return { field: left.var, op: op as ConditionOp, value: scalarToText(right) };
    }
  }
  return null;
}

function isVar(x: unknown): x is { var: string } {
  return !!x && typeof x === "object" && typeof (x as { var?: unknown }).var === "string";
}

/**
 * Try to decompose a stored expression into editor rows. Returns null when the
 * expression is too complex for the row editor (caller shows a raw JSON editor).
 */
export function parseExpr(expr: unknown): ConditionRow[] | null {
  if (expr === null || expr === undefined) return [];
  if (typeof expr !== "object") return null;
  const obj = expr as Record<string, unknown>;
  if (Array.isArray(obj.and)) {
    const rows = obj.and.map(parseClause);
    return rows.every(Boolean) ? (rows as ConditionRow[]) : null;
  }
  const single = parseClause(obj);
  return single ? [single] : null;
}

/** A short human summary of an expression for node labels. */
export function describeExpr(expr: unknown): string {
  const rows = parseExpr(expr);
  if (rows === null) return "custom rule";
  if (rows.length === 0) return "";
  return rows.map((r) => `${r.field} ${r.op} ${r.value}`).join(" AND ");
}
