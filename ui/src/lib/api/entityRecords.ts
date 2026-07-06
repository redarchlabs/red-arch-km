import apiClient from "./client";

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

export interface RecordListParams {
  /** Case-insensitive substring search across text columns. */
  search?: string;
  /** Opaque cursor from a prior response's ``next_cursor``. */
  cursor?: string | null;
  limit?: number;
}

export async function listRecords(
  slug: string,
  params: RecordListParams = {},
): Promise<RecordListResult> {
  const response = await apiClient.get<RecordListResult>(`/entities/${slug}/records`, {
    params: {
      q: params.search || undefined,
      cursor: params.cursor || undefined,
      limit: params.limit ?? 50,
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
