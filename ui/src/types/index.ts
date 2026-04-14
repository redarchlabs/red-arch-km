/**
 * Shared TypeScript types for the UI.
 */

export interface Org {
  id: string;
  name: string;
  description: string | null;
  use_knowledge_graph: boolean;
}

export interface UserProfile {
  id: string;
  username: string;
  email: string;
  is_site_admin: boolean;
}

export interface Folder {
  id: string;
  name: string;
  description: string | null;
  parent_id: string | null;
  dot_path: string;
  order: number;
  org_id: string;
}

export interface Document {
  id: string;
  title: string;
  description: string | null;
  document_key: string;
  processing_status: "PENDING" | "PROCESSING" | "COMPLETE" | "ERROR" | "STOPPED" | "DELETED";
  folder_id: string | null;
  org_id: string;
  created_at: string;
}

export interface Tag {
  id: string;
  name: string;
}

export interface ChatSession {
  id: string;
  chat_data: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}
