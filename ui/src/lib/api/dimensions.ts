import apiClient from "./client";

export type DimensionKind = "regions" | "departments" | "roles" | "groups";

export interface Dimension {
  id: string;
  name: string;
  description: string | null;
  permission_number: number;
}

export interface DimensionCreateInput {
  name: string;
  description?: string | null;
}

export const ADMIN_LIST_PAGE_SIZE = 200;

export interface DimensionListPage {
  items: Dimension[];
  total: number;
}

export async function listDimensions(kind: DimensionKind): Promise<DimensionListPage> {
  const response = await apiClient.get<{ items: Dimension[]; total: number }>(
    `/dimensions/${kind}`,
    { params: { page_size: ADMIN_LIST_PAGE_SIZE } },
  );
  return { items: response.data.items, total: response.data.total };
}

export async function createDimension(
  kind: DimensionKind,
  input: DimensionCreateInput,
): Promise<Dimension> {
  const response = await apiClient.post<Dimension>(`/dimensions/${kind}`, input);
  return response.data;
}

export async function updateDimension(
  kind: DimensionKind,
  id: string,
  input: DimensionCreateInput,
): Promise<Dimension> {
  const response = await apiClient.patch<Dimension>(`/dimensions/${kind}/${id}`, input);
  return response.data;
}

export async function deleteDimension(kind: DimensionKind, id: string): Promise<void> {
  await apiClient.delete(`/dimensions/${kind}/${id}`);
}
