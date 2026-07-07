/**
 * Views — composable screens rendered by the shared FormRenderer. Admin CRUD +
 * a render endpoint that resolves the view's element tree (reusing the form
 * render contract). Embedded forms (`form_ref`) are fetched client-side.
 */
import apiClient from "./client";
import type { FormConfig, FormRender } from "./forms";

export interface View {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  entity_definition_id: string | null;
  config: FormConfig;
  is_active: boolean;
}

export interface ViewCreateInput {
  name: string;
  slug: string;
  description?: string | null;
  entity_definition_id?: string | null;
  config?: FormConfig;
}

export interface ViewUpdateInput {
  name?: string;
  description?: string | null;
  config?: FormConfig;
  is_active?: boolean;
}

export async function listViews(): Promise<View[]> {
  return (await apiClient.get<View[]>("/views/")).data;
}
export async function getView(id: string): Promise<View> {
  return (await apiClient.get<View>(`/views/${id}`)).data;
}
export async function createView(input: ViewCreateInput): Promise<View> {
  return (await apiClient.post<View>("/views/", input)).data;
}
export async function updateView(id: string, input: ViewUpdateInput): Promise<View> {
  return (await apiClient.patch<View>(`/views/${id}`, input)).data;
}
export async function deleteView(id: string): Promise<void> {
  await apiClient.delete(`/views/${id}`);
}
export async function getViewRender(id: string, recordId?: string): Promise<FormRender> {
  return (
    await apiClient.get<FormRender>(`/views/${id}/render`, {
      params: recordId ? { record_id: recordId } : undefined,
    })
  ).data;
}
