import type { Folder } from "@/types";

import apiClient from "./client";

/**
 * A single permission rule. All dimensions are optional; an unset dimension
 * acts as "any" (wildcard) when the rule is evaluated server-side.
 */
export type PermissionRule = Partial<Record<"region" | "department" | "role" | "group", string>>;

export interface FolderCreateInput {
  name: string;
  description?: string | null;
  parent_id?: string | null;
  viewer_permissions_config?: PermissionRule[] | null;
  contributor_permissions_config?: PermissionRule[] | null;
}

export async function listFolders(): Promise<Folder[]> {
  // Backend paginates; request the max page size to keep the UI flat.
  const response = await apiClient.get<{ items: Folder[] }>("/folders/", {
    params: { page_size: 200 },
  });
  return response.data.items;
}

export async function createFolder(input: FolderCreateInput): Promise<Folder> {
  const response = await apiClient.post<Folder>("/folders/", input);
  return response.data;
}

export async function getFolder(id: string): Promise<Folder> {
  const response = await apiClient.get<Folder>(`/folders/${id}`);
  return response.data;
}

export interface FolderUpdateInput {
  name?: string;
  description?: string | null;
  parent_id?: string | null;
}

export async function updateFolder(id: string, input: FolderUpdateInput): Promise<Folder> {
  const response = await apiClient.patch<Folder>(`/folders/${id}`, input);
  return response.data;
}

export async function deleteFolder(id: string): Promise<void> {
  await apiClient.delete(`/folders/${id}`);
}
