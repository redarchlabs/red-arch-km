import type { Tag } from "@/types";

import apiClient from "./client";

export const ADMIN_LIST_PAGE_SIZE = 200;

export interface TagListPage {
  items: Tag[];
  total: number;
}

export async function listTags(): Promise<TagListPage> {
  const response = await apiClient.get<{ items: Tag[]; total: number }>("/tags/", {
    params: { page_size: ADMIN_LIST_PAGE_SIZE },
  });
  return { items: response.data.items, total: response.data.total };
}

export async function createTag(name: string): Promise<Tag> {
  const response = await apiClient.post<Tag>("/tags/", { name });
  return response.data;
}

export async function updateTag(id: string, name: string): Promise<Tag> {
  const response = await apiClient.patch<Tag>(`/tags/${id}`, { name });
  return response.data;
}

export async function deleteTag(id: string): Promise<void> {
  await apiClient.delete(`/tags/${id}`);
}
