/**
 * Single source of truth for server-side field-filter operators, mirroring the
 * backend's `FILTER_OPERATORS` (api/repositories/dynamic_entity.py) and
 * `FilterOp` Literal (api/schemas/aggregate.py). Imported by the records client,
 * the reports client, and the report builder so the set can't drift per-file.
 */
export type FilterOp = "eq" | "ne" | "gt" | "gte" | "lt" | "lte" | "in" | "contains" | "isnull";

export const FILTER_OPERATORS: readonly FilterOp[] = [
  "eq",
  "ne",
  "gt",
  "gte",
  "lt",
  "lte",
  "in",
  "contains",
  "isnull",
] as const;
