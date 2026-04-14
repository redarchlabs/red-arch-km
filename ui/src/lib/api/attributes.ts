import apiClient from "./client";

export type AttributeType = "freeform" | "picklist";

export interface AttributeDefinition {
  id: string;
  name: string;
  slug: string;
  attribute_type: AttributeType;
  picklist_options: string[];
  required: boolean;
  order: number;
}

export interface AttributeCreateInput {
  name: string;
  slug: string;
  attribute_type: AttributeType;
  picklist_options?: string[];
  required?: boolean;
  order?: number;
}

export interface AttributeUpdateInput {
  name?: string;
  attribute_type?: AttributeType;
  picklist_options?: string[];
  required?: boolean;
  order?: number;
}

export const ADMIN_LIST_PAGE_SIZE = 200;

export interface AttributeListPage {
  items: AttributeDefinition[];
  total: number;
}

export async function listAttributes(): Promise<AttributeListPage> {
  const response = await apiClient.get<{ items: AttributeDefinition[]; total: number }>(
    "/attributes/",
    { params: { page_size: ADMIN_LIST_PAGE_SIZE } },
  );
  return { items: response.data.items, total: response.data.total };
}

export async function createAttribute(
  input: AttributeCreateInput,
): Promise<AttributeDefinition> {
  const response = await apiClient.post<AttributeDefinition>("/attributes/", input);
  return response.data;
}

export async function updateAttribute(
  id: string,
  input: AttributeUpdateInput,
): Promise<AttributeDefinition> {
  const response = await apiClient.patch<AttributeDefinition>(`/attributes/${id}`, input);
  return response.data;
}

export async function deleteAttribute(id: string): Promise<void> {
  await apiClient.delete(`/attributes/${id}`);
}
