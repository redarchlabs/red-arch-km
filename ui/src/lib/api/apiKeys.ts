/**
 * API keys (org-admin) — programmatic credentials for the enterprise REST API
 * (`/api/v1`). Mirrors the backend contract in
 * `services/api/src/api/routers/api_keys.py` + `schemas/api_key.py`.
 *
 * The plaintext `key` is returned ONCE on creation; only its hash is stored. The
 * UI must surface it immediately and warn that it cannot be shown again.
 */
import apiClient from "./client";

export type ApiKeyStatus = "active" | "revoked" | "expired";

/** "org" = organization-wide knowledge; "scoped" = its own assignments below. */
export type ApiKeyAccessMode = "org" | "scoped";

/** An assignment shown on a key: a dimension's name, or a folder's path. */
export interface NamedRef {
  id: string;
  name: string;
}

export interface ApiKey {
  id: string;
  name: string;
  /** Non-secret display prefix, e.g. "km2_AbC123". */
  key_prefix: string;
  scopes: string[];
  status: ApiKeyStatus;
  created_by_profile_id: string | null;
  access_mode: ApiKeyAccessMode;
  regions: NamedRef[];
  roles: NamedRef[];
  groups: NamedRef[];
  departments: NamedRef[];
  /** Folders (with their subfolders) that narrow what the key reads. */
  folders: NamedRef[];
  /**
   * Set only for a scoped key with no assignments left: released after the key
   * was revoked/expired, or (an active key) broken and refused.
   */
  assignments_note: string | null;
  last_used_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
  created_at: string;
}

/** The create response — carries the one-time plaintext `key`. */
export interface ApiKeyCreated extends ApiKey {
  key: string;
}

/** One grantable scope + its description (drives the create form). */
export interface ScopeInfo {
  name: string;
  description: string;
}

export interface ApiKeyCreateInput {
  name: string;
  scopes: string[];
  /** ISO 8601 instant, or null/omitted for a non-expiring key. */
  expires_at?: string | null;
  /**
   * Dimension assignments: the key reads knowledge exactly as a member holding
   * them would. Any non-empty list (or folder_ids) makes the key "scoped";
   * all empty = an organization-wide key. Fixed once minted.
   */
  region_ids?: string[];
  role_ids?: string[];
  group_ids?: string[];
  department_ids?: string[];
  /** Folders (with subfolders) that only NARROW what the key reads; never grant. */
  folder_ids?: string[];
}

export async function listApiKeys(): Promise<ApiKey[]> {
  return (await apiClient.get<ApiKey[]>("/api-keys/")).data;
}

export async function listScopes(): Promise<ScopeInfo[]> {
  return (await apiClient.get<ScopeInfo[]>("/api-keys/scopes")).data;
}

export async function createApiKey(input: ApiKeyCreateInput): Promise<ApiKeyCreated> {
  return (await apiClient.post<ApiKeyCreated>("/api-keys/", input)).data;
}

export async function revokeApiKey(id: string): Promise<ApiKey> {
  return (await apiClient.delete<ApiKey>(`/api-keys/${id}`)).data;
}
