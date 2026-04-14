import type { Tag } from "@/types";

import apiClient from "./client";

export async function listTags(): Promise<Tag[]> {
  const response = await apiClient.get<Tag[]>("/tags/");
  return response.data;
}

export async function createTag(name: string): Promise<Tag> {
  const response = await apiClient.post<Tag>("/tags/", { name });
  return response.data;
}

export async function deleteTag(id: string): Promise<void> {
  await apiClient.delete(`/tags/${id}`);
}
