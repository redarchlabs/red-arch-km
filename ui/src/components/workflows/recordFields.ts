/** Helpers to convert between a flat record object and a friendly row editor. */

import type { EntityField, FieldType } from "@/lib/api/entities";

export interface RecordRow {
  key: string;
  value: string;
  /**
   * When set, this row pulls its value from a field on the triggering record
   * instead of the literal `value` — it serialises to `{ "$ref": "after.<ref>" }`.
   * `ref` holds the source field slug.
   */
  ref?: string;
}

/** Namespace of the triggering record's post-change state (the new/updated row). */
export const REF_PREFIX = "after.";
const REF_KEY = "$ref";

/**
 * If `value` is a trigger-field reference envelope (`{ "$ref": "after.<slug>" }`),
 * return the source field slug; otherwise null. Only the `after.` namespace is
 * surfaced in the UI (the newly created / updated record).
 */
export function parseRef(value: unknown): string | null {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return null;
  const obj = value as Record<string, unknown>;
  const raw = obj[REF_KEY];
  if (Object.keys(obj).length === 1 && typeof raw === "string" && raw.startsWith(REF_PREFIX)) {
    return raw.slice(REF_PREFIX.length);
  }
  return null;
}

/** Build the reference envelope stored for a "from trigger" row. */
export function makeRef(sourceSlug: string): Record<string, string> {
  return { [REF_KEY]: `${REF_PREFIX}${sourceSlug}` };
}

const NUMERIC: FieldType[] = ["integer", "bigint", "numeric"];

export function isNumericField(field: EntityField | undefined): boolean {
  return field !== undefined && NUMERIC.includes(field.field_type);
}

/** Index entity fields by slug for quick lookup. */
export function fieldMap(fields: EntityField[] | undefined): Map<string, EntityField> {
  const map = new Map<string, EntityField>();
  for (const field of fields ?? []) map.set(field.slug, field);
  return map;
}

export function scalarToString(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

function isScalar(value: unknown): boolean {
  return value === null || ["string", "number", "boolean"].includes(typeof value);
}

/**
 * Normalise a stored value (object, JSON string, or null/undefined) into a plain
 * object. Returns null when the value is a non-empty string that isn't a JSON
 * object, or a JSON array — cases the row editor can't represent.
 */
function toObject(value: unknown): Record<string, unknown> | null {
  if (value === null || value === undefined) return {};
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (trimmed === "") return {};
    try {
      const parsed = JSON.parse(trimmed);
      return parsed && typeof parsed === "object" && !Array.isArray(parsed)
        ? (parsed as Record<string, unknown>)
        : null;
    } catch {
      return null;
    }
  }
  if (typeof value === "object" && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return null;
}

/**
 * Decompose a stored value into editor rows. Returns null when the value can't
 * be represented as flat scalar rows (nested objects/arrays, or invalid JSON) —
 * the caller then falls back to a raw JSON editor.
 */
export function objectToRows(value: unknown): RecordRow[] | null {
  const obj = toObject(value);
  if (obj === null) return null;
  const rows: RecordRow[] = [];
  for (const [key, val] of Object.entries(obj)) {
    const ref = parseRef(val);
    if (ref !== null) {
      rows.push({ key, value: "", ref });
      continue;
    }
    if (!isScalar(val)) return null;
    rows.push({ key, value: scalarToString(val) });
  }
  return rows;
}

/** Coerce a row's string value to the type implied by its entity field. */
export function coerceValue(field: EntityField | undefined, raw: string): unknown {
  if (field === undefined) return raw;
  const trimmed = raw.trim();
  switch (field.field_type) {
    case "boolean":
      return trimmed === "true";
    case "integer":
    case "bigint": {
      // parseInt is lenient/lossy ("12abc" → 12, "12.9" → 12); require a whole
      // number and keep the raw string otherwise so the backend can validate it.
      if (trimmed === "") return raw;
      const n = Number(trimmed);
      return Number.isInteger(n) ? n : raw;
    }
    case "numeric": {
      const n = Number(trimmed);
      return trimmed === "" || Number.isNaN(n) ? raw : n;
    }
    case "json":
      try {
        return JSON.parse(raw);
      } catch {
        return raw;
      }
    default:
      return raw;
  }
}

/** Build a record object from editor rows, coercing each value by field type. */
export function rowsToObject(
  rows: RecordRow[],
  fields: EntityField[] | undefined,
): Record<string, unknown> {
  const map = fieldMap(fields);
  const obj: Record<string, unknown> = {};
  for (const row of rows) {
    const key = row.key.trim();
    if (!key) continue;
    obj[key] = row.ref ? makeRef(row.ref) : coerceValue(map.get(key), row.value);
  }
  return obj;
}

/**
 * Keys that appear more than once across the rows (trimmed, blanks ignored).
 * `rowsToObject` keeps only the last value for a duplicate key, so callers use
 * this to surface a warning rather than silently dropping the earlier rows.
 */
export function findDuplicateKeys(rows: RecordRow[]): string[] {
  const seen = new Set<string>();
  const duplicates = new Set<string>();
  for (const row of rows) {
    const key = row.key.trim();
    if (!key) continue;
    if (seen.has(key)) duplicates.add(key);
    else seen.add(key);
  }
  return [...duplicates];
}
