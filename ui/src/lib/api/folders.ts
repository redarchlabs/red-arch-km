import type { Folder } from "@/types";

import apiClient from "./client";

export interface FolderCreateInput {
  name: string;
  description?: string | null;
  parent_id?: string | null;
  viewer_permissions_config?: Array<Record<string, string>> | null;
  contributor_permissions_config?: Array<Record<string, string>> | null;
}

export async function listFolders(): Promise<Folder[]> {
  const response = await apiClient.get<Folder[]>("/folders/");
  return response.data;
}

export async function createFolder(input: FolderCreateInput): Promise<Folder> {
  const response = await apiClient.post<Folder>("/folders/", input);
  return response.data;
}

export async function getFolder(id: string): Promise<Folder> {
  const response = await apiClient.get<Folder>(`/folders/${id}`);
  return response.data;
}
