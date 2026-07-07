/**
 * Client evaluator for `calculated` form elements — a TypeScript port of the
 * backend `services/form_expression.py`, kept in lock-step so a value previews
 * on the client exactly as the server will recompute + persist it. The op set is
 * a whitelisted subset (var, arithmetic, comparison, if/and/or/not, cat, and the
 * date ops today/now/date_add/date_diff): no code execution, no property access.
 *
 * The server is authoritative for any persisted calculated value; this exists so
 * the filler sees the right number/date live as they type. On any error a formula
 * degrades to `null` rather than throwing.
 */

export type ExprContext = Record<string, unknown>;

const UNIT_DAYS: Record<string, number> = { day: 1, week: 7 };
const MONTH_UNITS: Record<string, number> = { month: 1, year: 12 };

export function evaluate(expr: unknown, context: ExprContext): unknown {
  try {
    return evalNode(expr, context);
  } catch {
    return null;
  }
}

function evalNode(expr: unknown, ctx: ExprContext): unknown {
  if (expr === null || typeof expr !== "object" || Array.isArray(expr)) {
    return expr; // literal
  }
  const keys = Object.keys(expr as Record<string, unknown>);
  if (keys.length !== 1) return expr;
  const op = keys[0];
  const raw = (expr as Record<string, unknown>)[op];
  const args = Array.isArray(raw) ? raw : [raw];
  const ev = args.map((a) => evalNode(a, ctx));

  switch (op) {
    case "var":
      return getVar(ctx, ev[0]);
    case "cat":
      return ev.map((v) => (v == null ? "" : String(v))).join("");
    case "if": {
      let i = 0;
      while (i + 1 < ev.length) {
        if (truthy(ev[i])) return ev[i + 1];
        i += 2;
      }
      return i < ev.length ? ev[i] : null;
    }
    case "and": {
      let result: unknown = true;
      for (const v of ev) {
        if (!truthy(v)) return v;
        result = v;
      }
      return result;
    }
    case "or": {
      for (const v of ev) if (truthy(v)) return v;
      return ev.length ? ev[ev.length - 1] : null;
    }
    case "!":
      return !truthy(ev[0]);
    case "==":
    case "!=":
    case "<":
    case "<=":
    case ">":
    case ">=":
      return compare(op, ev[0], ev[1]);
    case "+":
    case "-":
    case "*":
    case "/":
      return arith(op, ev);
    case "today":
      return new Date().toISOString().slice(0, 10);
    case "now":
      return new Date().toISOString();
    case "date_add":
      return dateAdd(ev);
    case "date_diff":
      return dateDiff(ev);
    default:
      throw new Error(`unknown operator: ${op}`);
  }
}

function getVar(data: ExprContext, path: unknown): unknown {
  if (path === "" || path == null) return data;
  let current: unknown = data;
  for (const part of String(path).split(".")) {
    if (current != null && typeof current === "object" && part in (current as object)) {
      current = (current as Record<string, unknown>)[part];
    } else {
      return null;
    }
  }
  return current;
}

function truthy(value: unknown): boolean {
  if (Array.isArray(value)) return value.length > 0;
  return Boolean(value);
}

function num(value: unknown): number | null {
  if (typeof value === "boolean") return value ? 1 : 0;
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string" && value.trim() !== "") {
    const n = Number(value.trim());
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

function compare(op: string, a: unknown, b: unknown): boolean {
  const na = num(a);
  const nb = num(b);
  let x: unknown = a;
  let y: unknown = b;
  if (na !== null && nb !== null) {
    x = na;
    y = nb;
  }
  if (op === "==") return x === y;
  if (op === "!=") return x !== y;
  if (x == null || y == null) return false;
  if (op === "<") return (x as number) < (y as number);
  if (op === "<=") return (x as number) <= (y as number);
  if (op === ">") return (x as number) > (y as number);
  return (x as number) >= (y as number);
}

function arith(op: string, ev: unknown[]): number | null {
  const nums = ev.map(num);
  if (nums.length === 0 || nums.some((n) => n === null)) return null;
  let acc = nums[0] as number;
  for (const n of nums.slice(1) as number[]) {
    if (op === "+") acc += n;
    else if (op === "-") acc -= n;
    else if (op === "*") acc *= n;
    else {
      if (n === 0) return null;
      acc /= n;
    }
  }
  return acc;
}

function toDate(value: unknown): Date | null {
  if (value instanceof Date) return value;
  if (typeof value === "string" && value) {
    const s = value.length <= 10 ? `${value}T00:00:00Z` : value.replace(" ", "T");
    const d = new Date(s);
    return Number.isNaN(d.getTime()) ? null : d;
  }
  return null;
}

function addMonths(d: Date, months: number): Date {
  const year = d.getUTCFullYear();
  const month = d.getUTCMonth() + months;
  const targetYear = year + Math.floor(month / 12);
  const targetMonth = ((month % 12) + 12) % 12;
  const lastDay = new Date(Date.UTC(targetYear, targetMonth + 1, 0)).getUTCDate();
  const day = Math.min(d.getUTCDate(), lastDay);
  return new Date(Date.UTC(targetYear, targetMonth, day));
}

function isoDate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function dateAdd(ev: unknown[]): string | null {
  if (ev.length < 3) return null;
  const d = toDate(ev[0]);
  const amount = num(ev[1]);
  const unit = String(ev[2]);
  if (d === null || amount === null) return null;
  const n = Math.trunc(amount);
  if (unit in UNIT_DAYS) {
    const out = new Date(d.getTime());
    out.setUTCDate(out.getUTCDate() + n * UNIT_DAYS[unit]);
    return isoDate(out);
  }
  if (unit in MONTH_UNITS) return isoDate(addMonths(d, n * MONTH_UNITS[unit]));
  return null;
}

function dateDiff(ev: unknown[]): number | null {
  if (ev.length < 2) return null;
  const a = toDate(ev[0]);
  const b = toDate(ev[1]);
  if (a === null || b === null) return null;
  return Math.round((a.getTime() - b.getTime()) / 86400000);
}
