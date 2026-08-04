import type { Org } from "@/types";

import apiClient from "./client";

export interface OrgCreateInput {
  name: string;
  description?: string | null;
  use_knowledge_graph?: boolean;
}

/** Site-admin org fields. The home view lives on {@link OrgSettingsInput}. */
export interface OrgUpdateInput {
  name?: string;
  description?: string | null;
  use_knowledge_graph?: boolean;
  /** Model id pins the org's LLM; empty string clears to the platform default; omit for no change. */
  default_llm_model?: string;
}

/**
 * Org-admin-writable settings (`PATCH /api/orgs/{id}/settings`). Replacement
 * semantics, not patch semantics: `null` clears the org's home view.
 */
export interface OrgSettingsInput {
  home_view_id: string | null;
}

/** Model ids an org can be pinned to, plus the platform default. */
export interface LlmModelCatalog {
  default: string;
  models: string[];
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

export async function listLlmModels(): Promise<LlmModelCatalog> {
  const response = await apiClient.get<LlmModelCatalog>("/orgs/llm-models");
  return response.data;
}

export async function updateOrg(id: string, input: OrgUpdateInput): Promise<Org> {
  const response = await apiClient.patch<Org>(`/orgs/${id}`, input);
  return response.data;
}

/** Org-admin path: requires an org-admin membership in the org, not site admin. */
export async function updateOrgSettings(id: string, input: OrgSettingsInput): Promise<Org> {
  const response = await apiClient.patch<Org>(`/orgs/${id}/settings`, input);
  return response.data;
}

export async function deleteOrg(id: string): Promise<void> {
  await apiClient.delete(`/orgs/${id}`);
}
