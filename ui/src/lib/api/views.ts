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
  /** Anonymous access state. The token itself is never returned — a lost link is
   * rotated, not recovered. Null/absent means sharing is off. */
  public_enabled_at?: string | null;
  public_record_id?: string | null;
  public_expires_at?: string | null;
}

export interface ViewShareCreated {
  url: string;
  token: string;
  expires_at: string | null;
  record_id: string | null;
  /** Element types on this view that need a login to fetch their own data. */
  unsupported_elements: string[];
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

// ---- anonymous access (org-admin to manage; token-only to use) ----

/** Turn on anonymous access for one view, or ROTATE its link. The raw token
 * comes back exactly once; rotating invalidates the previous link immediately. */
export async function enableViewShare(
  id: string,
  input: { record_id?: string | null; expires_at?: string | null },
): Promise<ViewShareCreated> {
  return (await apiClient.post<ViewShareCreated>(`/views/${id}/share`, input)).data;
}

/** Revoke anonymous access. The existing link stops working immediately. */
export async function disableViewShare(id: string): Promise<View> {
  return (await apiClient.delete<View>(`/views/${id}/share`)).data;
}

// The two public calls below are the ONLY ones a shared page makes. They carry no
// credentials beyond the token in the path, so they deliberately bypass the
// authenticated client's org header and auth interceptors.
const PUBLIC_BASE = `${apiClient.defaults.baseURL ?? "/api"}/public/views`;

export async function getPublicViewRender(token: string): Promise<FormRender> {
  const res = await fetch(`${PUBLIC_BASE}/${encodeURIComponent(token)}`, { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(res.status === 404 ? "This link is not available." : `Failed to load (${res.status})`);
  return (await res.json()) as FormRender;
}

/** Run one of the shared view's OWN workflows. The server rejects any workflow
 * the view's element tree doesn't reference, so this cannot be widened here. */
export async function runPublicViewWorkflow(
  token: string,
  workflowId: string,
  body: { inputs?: Record<string, unknown>; after?: Record<string, unknown> },
): Promise<void> {
  const res = await fetch(
    `${PUBLIC_BASE}/${encodeURIComponent(token)}/workflows/${encodeURIComponent(workflowId)}/run`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  if (!res.ok) throw new Error(`Workflow failed to start (${res.status})`);
}
