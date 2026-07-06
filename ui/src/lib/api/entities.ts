import apiClient from "./client";

export type FieldType =
  | "text"
  | "long_text"
  | "integer"
  | "bigint"
  | "numeric"
  | "boolean"
  | "date"
  | "timestamptz"
  | "uuid"
  | "json"
  | "picklist";

export type Cardinality =
  | "one_to_one"
  | "one_to_many"
  | "many_to_one"
  | "many_to_many";

export type OnDelete = "CASCADE" | "SET NULL" | "RESTRICT";

export interface EntityField {
  id: string;
  name: string;
  slug: string;
  field_type: FieldType;
  picklist_options: string[] | null;
  is_required: boolean;
  is_unique: boolean;
  default_value: unknown;
  order: number;
}

export interface EntityRelationship {
  id: string;
  name: string;
  slug: string;
  cardinality: Cardinality;
  on_delete: OnDelete;
  is_required: boolean;
  source_definition_id: string;
  target_definition_id: string;
}

export interface EntityDefinition {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  is_active: boolean;
  fields: EntityField[];
}

export interface EntityFieldInput {
  name: string;
  slug: string;
  field_type: FieldType;
  picklist_options?: string[];
  is_required?: boolean;
  is_unique?: boolean;
  default_value?: unknown;
  order?: number;
}

export interface EntityDefinitionCreateInput {
  name: string;
  slug: string;
  description?: string | null;
  fields?: EntityFieldInput[];
}

export interface EntityRelationshipInput {
  name: string;
  slug: string;
  cardinality: Cardinality;
  target_definition_id: string;
  on_delete?: OnDelete;
  is_required?: boolean;
}

interface Paginated<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

const LIST_PAGE_SIZE = 200;

export async function listEntities(): Promise<EntityDefinition[]> {
  const response = await apiClient.get<Paginated<EntityDefinition>>("/entity-definitions/", {
    params: { page_size: LIST_PAGE_SIZE },
  });
  return response.data.items;
}

export async function getEntity(id: string): Promise<EntityDefinition> {
  const response = await apiClient.get<EntityDefinition>(`/entity-definitions/${id}`);
  return response.data;
}

export async function createEntity(input: EntityDefinitionCreateInput): Promise<EntityDefinition> {
  const response = await apiClient.post<EntityDefinition>("/entity-definitions/", input);
  return response.data;
}

export async function updateEntity(
  id: string,
  input: { name?: string; description?: string | null; is_active?: boolean },
): Promise<EntityDefinition> {
  const response = await apiClient.patch<EntityDefinition>(`/entity-definitions/${id}`, input);
  return response.data;
}

export async function deleteEntity(id: string, force = false): Promise<void> {
  await apiClient.delete(`/entity-definitions/${id}`, { params: { force } });
}

export async function addEntityField(id: string, input: EntityFieldInput): Promise<EntityField> {
  const response = await apiClient.post<EntityField>(`/entity-definitions/${id}/fields`, input);
  return response.data;
}

export async function listRelationships(id: string): Promise<EntityRelationship[]> {
  const response = await apiClient.get<EntityRelationship[]>(
    `/entity-definitions/${id}/relationships`,
  );
  return response.data;
}

export async function createRelationship(
  id: string,
  input: EntityRelationshipInput,
): Promise<EntityRelationship> {
  const response = await apiClient.post<EntityRelationship>(
    `/entity-definitions/${id}/relationships`,
    input,
  );
  return response.data;
}
