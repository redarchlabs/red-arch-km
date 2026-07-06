/** Helpers to convert between a friendly rule-row editor and JsonLogic. */

export const CONDITION_OPS = ["==", "!=", ">", ">=", "<", "<=", "in"] as const;
export type ConditionOp = (typeof CONDITION_OPS)[number];

export interface ConditionRow {
  field: string; // a var path, e.g. "after.status"
  op: ConditionOp;
  value: string;
}

export type JsonLogic = Record<string, unknown> | null;

function coerce(value: string): unknown {
  const trimmed = value.trim();
  if (trimmed === "") return "";
  if (trimmed === "true") return true;
  if (trimmed === "false") return false;
  const n = Number(trimmed);
  return Number.isNaN(n) ? trimmed : n;
}

function compileRow(row: ConditionRow): JsonLogic {
  const varRef = { var: row.field };
  if (row.op === "in") {
    const list = row.value.split(",").map((v) => coerce(v));
    return { in: [varRef, list] };
  }
  return { [row.op]: [varRef, coerce(row.value)] };
}

/** Compile AND-combined rows to a JsonLogic expression (null = always true). */
export function compileRows(rows: ConditionRow[]): JsonLogic {
  const valid = rows.filter((r) => r.field.trim() !== "");
  if (valid.length === 0) return null;
  if (valid.length === 1) return compileRow(valid[0]);
  return { and: valid.map(compileRow) };
}

function parseClause(clause: unknown): ConditionRow | null {
  if (!clause || typeof clause !== "object") return null;
  const entries = Object.entries(clause as Record<string, unknown>);
  if (entries.length !== 1) return null;
  const [op, args] = entries[0];
  if (op === "in" && Array.isArray(args) && args.length === 2) {
    const [needle, haystack] = args;
    if (isVar(needle) && Array.isArray(haystack)) {
      return { field: needle.var, op: "in", value: haystack.join(", ") };
    }
    return null;
  }
  if ((CONDITION_OPS as readonly string[]).includes(op) && Array.isArray(args) && args.length === 2) {
    const [left, right] = args;
    if (isVar(left)) {
      return { field: left.var, op: op as ConditionOp, value: String(right) };
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
