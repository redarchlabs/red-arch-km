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
 * Org-admin-writable settings (`PATCH /api/orgs/{id}/settings`). Per-field patch
 * semantics: an omitted key is "no change", an explicit `null` clears that one
 * setting. (So saving the home view no longer wipes branding, and vice versa.)
 */
export interface OrgSettingsInput {
  home_view_id?: string | null;
  /** `#rrggbb`, or null to clear back to the theme's own primary. */
  accent_color?: string | null;
}

/** Upload the org's logo (org admin). Replaces any previous one. */
export async function uploadOrgLogo(id: string, file: File): Promise<Org> {
  const body = new FormData();
  body.append("file", file);
  return (await apiClient.put<Org>(`/orgs/${id}/settings/logo`, body)).data;
}

/** Clear the org's logo. */
export async function deleteOrgLogo(id: string): Promise<Org> {
  return (await apiClient.delete<Org>(`/orgs/${id}/settings/logo`)).data;
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
