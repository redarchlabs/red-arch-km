import type { Org } from "@/types";

import apiClient from "./client";

export interface OrgCreateInput {
  name: string;
  description?: string | null;
  use_knowledge_graph?: boolean;
}

export interface OrgUpdateInput {
  name?: string;
  description?: string | null;
  use_knowledge_graph?: boolean;
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
