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
  folderId?: string | null,
): Promise<PaginatedResponse<Document>> {
  const response = await apiClient.get<PaginatedResponse<Document>>("/documents/", {
    // When folderId is set the backend scopes to that folder's contents;
    // otherwise it returns all visible docs plus unfiled ones.
    params: { page, page_size: pageSize, ...(folderId ? { folder_id: folderId } : {}) },
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

export type TranslationMethod = "ocr" | "ai";

export interface DocumentUploadInput {
  file: File;
  title: string;
  description?: string | null;
  folder_id?: string | null;
  /** "ocr" = free Tesseract; "ai" = OpenAI vision (handles scanned/complex docs). */
  translation_method?: TranslationMethod;
}

export async function uploadDocument(input: DocumentUploadInput): Promise<Document> {
  const form = new FormData();
  form.append("file", input.file);
  form.append("title", input.title);
  if (input.description) form.append("description", input.description);
  if (input.folder_id) form.append("folder_id", input.folder_id);
  form.append("translation_method", input.translation_method ?? "ocr");

  // Must let the browser/axios compute the multipart Content-Type *with its
  // boundary* — setting it manually (even to "multipart/form-data") omits the
  // boundary and the server can't parse the body. Passing undefined suppresses
  // the client's JSON default so the FormData branch sets it correctly.
  const response = await apiClient.post<Document>("/documents/upload", form, {
    headers: { "Content-Type": undefined },
  });
  return response.data;
}

export async function deleteDocument(id: string): Promise<void> {
  await apiClient.delete(`/documents/${id}`);
}

export interface DocumentChunk {
  id: string;
  text: string;
  /** Per-chunk summary (used by the reader's embedded/side-by-side views). */
  summary: string;
  chunk_order: number;
}

export interface DocumentChunksResponse {
  document_key: string;
  /** Total chunks in the document — lets the reader know when to stop paging. */
  total: number;
  offset: number;
  limit: number;
  chunks: DocumentChunk[];
}

/**
 * Fetch one page of a document's chunks. Paginated (offset/limit) so a very
 * large document can be lazy-loaded a page at a time instead of all at once.
 */
export async function getDocumentChunks(
  id: string,
  { offset = 0, limit = 50 }: { offset?: number; limit?: number } = {},
): Promise<DocumentChunksResponse> {
  const response = await apiClient.get<DocumentChunksResponse>(`/documents/${id}/chunks`, {
    params: { offset, limit },
  });
  return response.data;
}

/** One node in the hierarchical document-summary tree. */
export interface SummaryTreeNode {
  summary: string;
  children: SummaryTreeNode[];
}

export interface DocumentSummaryResponse {
  document_key: string;
  summary: string;
  summary_tree: SummaryTreeNode | null;
}

export async function getDocumentSummary(id: string): Promise<DocumentSummaryResponse> {
  const response = await apiClient.get<DocumentSummaryResponse>(`/documents/${id}/summary`);
  return response.data;
}

export interface DocumentContentResponse {
  /** Original text for markdown/text kinds; null for binary originals. */
  content: string | null;
  format: "markdown" | "text" | null;
  /** How the reader should display this document. */
  kind: "markdown" | "text" | "pdf" | "image" | "other";
  /** Short-lived signed URL to the original file (for pdf/image kinds). */
  original_url: string | null;
}

/** Fetch a document's original formatted text (for readable display). */
export async function getDocumentContent(id: string): Promise<DocumentContentResponse> {
  const response = await apiClient.get<DocumentContentResponse>(`/documents/${id}/content`);
  return response.data;
}
