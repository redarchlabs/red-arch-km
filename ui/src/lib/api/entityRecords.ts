import apiClient from "./client";
import type { FilterOp } from "./filterOps";

/** A dynamic entity record: base columns plus field slugs → values. */
export type EntityRecord = Record<string, unknown> & {
  id: string;
  created_at?: string;
  updated_at?: string;
};

export interface RecordListResult {
  items: EntityRecord[];
  /** Opaque token for the next page, or null on the last page. */
  next_cursor: string | null;
  limit: number;
}

/** Server-side field-filter operators. Aliased from the shared {@link FilterOp}. */
export type RecordFilterOp = FilterOp;

/** One server-side field filter. ``value`` is omitted for ``isnull`` (defaults to
 * "is null"); for ``in`` it is a comma-separated list. */
export interface RecordFilter {
  field: string;
  op: RecordFilterOp;
  value?: string;
}

/** Serialize a filter to the endpoint's ``field:op[:value]`` wire form. */
export function filterToParam(f: RecordFilter): string {
  return f.value === undefined || f.value === "" ? `${f.field}:${f.op}` : `${f.field}:${f.op}:${f.value}`;
}

export interface RecordListParams {
  /** Case-insensitive substring search across text columns. */
  search?: string;
  /** Server-side field filters, ANDed together. */
  filters?: RecordFilter[];
  /** Opaque cursor from a prior response's ``next_cursor``. */
  cursor?: string | null;
  limit?: number;
  /** Field slug (or base column) to sort by; overrides the default created_at sort. */
  orderBy?: string;
  /** Sort direction when orderBy is set. */
  orderDir?: "asc" | "desc";
}

export async function listRecords(
  slug: string,
  params: RecordListParams = {},
): Promise<RecordListResult> {
  const filters = (params.filters ?? []).filter((f) => f.field && f.op);
  const response = await apiClient.get<RecordListResult>(`/entities/${slug}/records`, {
    // indexes:null → repeated `filter=a&filter=b` (no `[]`), which FastAPI's
    // `filter: list[str]` query binds; the default bracket form would not.
    paramsSerializer: { indexes: null },
    params: {
      q: params.search || undefined,
      filter: filters.length ? filters.map(filterToParam) : undefined,
      cursor: params.cursor || undefined,
      limit: params.limit ?? 50,
      order_by: params.orderBy || undefined,
      order_dir: params.orderBy ? (params.orderDir ?? "desc") : undefined,
    },
  });
  return response.data;
}

export async function createRecord(
  slug: string,
  data: Record<string, unknown>,
): Promise<EntityRecord> {
  const response = await apiClient.post<EntityRecord>(`/entities/${slug}/records`, data);
  return response.data;
}

export async function updateRecord(
  slug: string,
  recordId: string,
  data: Record<string, unknown>,
): Promise<EntityRecord> {
  const response = await apiClient.patch<EntityRecord>(
    `/entities/${slug}/records/${recordId}`,
    data,
  );
  return response.data;
}

export async function deleteRecord(slug: string, recordId: string): Promise<void> {
  await apiClient.delete(`/entities/${slug}/records/${recordId}`);
}
