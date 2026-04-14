import type { ChatSession } from "@/types";

import apiClient from "./client";

export async function listSessions(): Promise<ChatSession[]> {
  const response = await apiClient.get<{ items: ChatSession[] }>("/chat/sessions", {
    params: { page_size: 200 },
  });
  return response.data.items;
}

export async function createSession(
  chat_data?: Record<string, unknown>,
): Promise<ChatSession> {
  const response = await apiClient.post<ChatSession>("/chat/sessions", { chat_data });
  return response.data;
}

export async function getSession(id: string): Promise<ChatSession> {
  const response = await apiClient.get<ChatSession>(`/chat/sessions/${id}`);
  return response.data;
}

export async function updateSession(
  id: string,
  chat_data: Record<string, unknown>,
): Promise<ChatSession> {
  const response = await apiClient.patch<ChatSession>(`/chat/sessions/${id}`, { chat_data });
  return response.data;
}

export async function deleteSession(id: string): Promise<void> {
  await apiClient.delete(`/chat/sessions/${id}`);
}
