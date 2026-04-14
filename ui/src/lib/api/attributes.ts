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

export async function listAttributes(): Promise<AttributeDefinition[]> {
  const response = await apiClient.get<{ items: AttributeDefinition[] }>("/attributes/", {
    params: { page_size: 200 },
  });
  return response.data.items;
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
