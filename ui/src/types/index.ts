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

/**
 * A single permission rule. All dimensions are optional; an unset dimension
 * means "any". A document/folder is visible/contributable if the user matches
 * any rule in the list.
 */
export type PermissionRule = Partial<
  Record<"region" | "department" | "role" | "group", string>
>;

export interface Folder {
  id: string;
  name: string;
  description: string | null;
  parent_id: string | null;
  dot_path: string;
  order: number;
  org_id: string;
  created_at: string | null;
  viewer_permissions_config: PermissionRule[] | null;
  contributor_permissions_config: PermissionRule[] | null;
}

export interface Document {
  id: string;
  title: string;
  description: string | null;
  document_key: string;
  // Canonical values written by the worker status callback. Must match
  // api ProcessingStatus enum (services/api/src/api/models/document.py).
  processing_status: "PENDING" | "PROCESSING" | "SUCCESS" | "FAILED";
  folder_id: string | null;
  org_id: string;
  size_bytes: number | null;
  viewer_permissions_config: PermissionRule[] | null;
  contributor_permissions_config: PermissionRule[] | null;
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
