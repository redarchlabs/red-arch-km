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

export async function listDimensions(kind: DimensionKind): Promise<Dimension[]> {
  const response = await apiClient.get<{ items: Dimension[] }>(`/dimensions/${kind}`, {
    params: { page_size: 200 },
  });
  return response.data.items;
}

export async function createDimension(
  kind: DimensionKind,
  input: DimensionCreateInput,
): Promise<Dimension> {
  const response = await apiClient.post<Dimension>(`/dimensions/${kind}`, input);
  return response.data;
}

export async function deleteDimension(kind: DimensionKind, id: string): Promise<void> {
  await apiClient.delete(`/dimensions/${kind}/${id}`);
}
