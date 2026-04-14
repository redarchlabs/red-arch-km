import type { Document, PaginatedResponse } from "@/types";

import apiClient from "./client";

export interface DocumentCreateInput {
  title: string;
  description?: string | null;
  text?: string | null;
  folder_id?: string | null;
  tag_ids?: string[];
  metadata?: Record<string, unknown>;
  use_knowledge_graph?: boolean | null;
}

export async function listDocuments(
  page = 1,
  pageSize = 20,
): Promise<PaginatedResponse<Document>> {
  const response = await apiClient.get<PaginatedResponse<Document>>("/documents/", {
    params: { page, page_size: pageSize },
  });
  return response.data;
}

export async function getDocument(id: string): Promise<Document> {
  const response = await apiClient.get<Document>(`/documents/${id}`);
  return response.data;
}

export async function createDocument(input: DocumentCreateInput): Promise<Document> {
  const response = await apiClient.post<Document>("/documents/", input);
  return response.data;
}

export async function deleteDocument(id: string): Promise<void> {
  await apiClient.delete(`/documents/${id}`);
}

export interface DocumentChunk {
  id: string;
  text: string;
  chunk_order: number;
}

export interface DocumentChunksResponse {
  document_key: string;
  chunks: DocumentChunk[];
}

export async function getDocumentChunks(id: string): Promise<DocumentChunksResponse> {
  const response = await apiClient.get<DocumentChunksResponse>(`/documents/${id}/chunks`);
  return response.data;
}
