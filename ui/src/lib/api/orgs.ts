import type { Org } from "@/types";

import apiClient from "./client";

/**
 * All-zero UUID sentinel understood by `PATCH /api/orgs/{id}` to CLEAR the
 * home view. Omitting `home_view_id` (or sending undefined) means "no change";
 * a real UUID sets it; this sentinel clears it.
 */
export const NIL_UUID = "00000000-0000-0000-0000-000000000000";

export interface OrgCreateInput {
  name: string;
  description?: string | null;
  use_knowledge_graph?: boolean;
}

export interface OrgUpdateInput {
  name?: string;
  description?: string | null;
  use_knowledge_graph?: boolean;
  /** Real UUID sets the home view; `NIL_UUID` clears it; omit for no change. */
  home_view_id?: string | null;
}

export async function listOrgs(): Promise<Org[]> {
  // Site-admin list of every org; paginated server-side, requested with
  // the max page size since the total org count is typically small.
  const response = await apiClient.get<{ items: Org[] }>("/orgs/", {
    params: { page_size: 200 },
  });
  return response.data.items;
}

export async function getOrg(id: string): Promise<Org> {
  const response = await apiClient.get<Org>(`/orgs/${id}`);
  return response.data;
}

export async function createOrg(input: OrgCreateInput): Promise<Org> {
  const response = await apiClient.post<Org>("/orgs/", input);
  return response.data;
}

export async function updateOrg(id: string, input: OrgUpdateInput): Promise<Org> {
  const response = await apiClient.patch<Org>(`/orgs/${id}`, input);
  return response.data;
}

export async function deleteOrg(id: string): Promise<void> {
  await apiClient.delete(`/orgs/${id}`);
}
