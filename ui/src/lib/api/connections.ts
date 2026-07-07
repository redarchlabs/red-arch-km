/**
 * Connector credentials (org-admin) — the reusable HTTP connections an
 * `http_request` task authenticates through. Mirrors the backend contract in
 * `services/api/src/api/routers/workflows.py` + `schemas/workflow.py`.
 *
 * The secret is WRITE-ONLY: it is never returned by the API. `has_secret` tells
 * the UI whether one is stored, and a new secret is sent only when the operator
 * actually types one (see {@link buildConnectionUpdate}).
 */
import apiClient from "./client";

export type ConnectionAuthType = "none" | "bearer" | "api_key" | "basic";

export const CONNECTION_AUTH_TYPES: readonly ConnectionAuthType[] = [
  "none",
  "bearer",
  "api_key",
  "basic",
];

export const CONNECTION_AUTH_LABELS: Record<ConnectionAuthType, string> = {
  none: "No auth",
  bearer: "Bearer token",
  api_key: "API key header",
  basic: "Basic auth",
};

export interface Connection {
  id: string;
  name: string;
  kind: string;
  base_url: string | null;
  auth_type: ConnectionAuthType;
  config: Record<string, unknown>;
  /** True when an encrypted secret is stored (the secret itself is never returned). */
  has_secret: boolean;
}

export interface ConnectionCreateInput {
  name: string;
  /** Connector kind; only "http" is supported today (server default). */
  kind?: string;
  base_url?: string | null;
  auth_type: ConnectionAuthType;
  secret?: string;
  config?: Record<string, unknown>;
}

export interface ConnectionUpdateInput {
  name?: string;
  base_url?: string | null;
  auth_type?: ConnectionAuthType;
  secret?: string;
  config?: Record<string, unknown>;
}

// --------------------------------------------------------------------------- //
// Pure form <-> payload logic (unit-tested; no React, no network)
// --------------------------------------------------------------------------- //

/** The editable shape backing the create/edit form. */
export interface ConnectionFormState {
  name: string;
  base_url: string;
  auth_type: ConnectionAuthType;
  /** Current value of the secret input (blank = keep the stored one on edit). */
  secret: string;
  /** True once the operator edits the secret field — gates sending a new secret. */
  secretDirty: boolean;
  /** Auth-specific extras: `header` (api_key) / `username` (basic). */
  config: Record<string, unknown>;
}

/** Blank form for creating a fresh connection. */
export const EMPTY_CONNECTION_FORM: ConnectionFormState = {
  name: "",
  base_url: "",
  auth_type: "none",
  secret: "",
  secretDirty: false,
  config: {},
};

/** Seed a form from an existing connection (secret always starts blank). */
export function connectionToForm(conn: Connection): ConnectionFormState {
  return {
    name: conn.name,
    base_url: conn.base_url ?? "",
    auth_type: conn.auth_type,
    secret: "",
    secretDirty: false,
    config: { ...conn.config },
  };
}

/**
 * Keep only the config keys the chosen auth type actually uses, so switching
 * auth types never persists a stale `header`/`username`. Trims blanks away.
 */
export function normalizeConnectionConfig(
  authType: ConnectionAuthType,
  config: Record<string, unknown>,
): Record<string, unknown> {
  if (authType === "api_key") {
    const header = String(config.header ?? "").trim();
    return header ? { header } : {};
  }
  if (authType === "basic") {
    const username = String(config.username ?? "").trim();
    return username ? { username } : {};
  }
  return {};
}

function baseFields(form: ConnectionFormState) {
  const base_url = form.base_url.trim();
  return {
    name: form.name.trim(),
    base_url: base_url === "" ? null : base_url,
    auth_type: form.auth_type,
    config: normalizeConnectionConfig(form.auth_type, form.config),
  };
}

/** Build the POST body for a new connection. Secret sent only when non-empty. */
export function buildConnectionCreate(form: ConnectionFormState): ConnectionCreateInput {
  const body: ConnectionCreateInput = { kind: "http", ...baseFields(form) };
  if (form.secret !== "") body.secret = form.secret;
  return body;
}

/**
 * Build the PATCH body for an existing connection. The secret is included ONLY
 * when the operator typed a new one (`secretDirty` + non-empty); otherwise it is
 * omitted so the backend keeps the stored secret untouched.
 */
export function buildConnectionUpdate(form: ConnectionFormState): ConnectionUpdateInput {
  const body: ConnectionUpdateInput = { ...baseFields(form) };
  if (form.secretDirty && form.secret !== "") body.secret = form.secret;
  return body;
}

// --------------------------------------------------------------------------- //
// Network
// --------------------------------------------------------------------------- //
export async function listConnections(): Promise<Connection[]> {
  return (await apiClient.get<Connection[]>("/workflows/connections")).data;
}

export async function createConnection(input: ConnectionCreateInput): Promise<Connection> {
  return (await apiClient.post<Connection>("/workflows/connections", input)).data;
}

export async function updateConnection(
  id: string,
  input: ConnectionUpdateInput,
): Promise<Connection> {
  return (await apiClient.patch<Connection>(`/workflows/connections/${id}`, input)).data;
}

export async function deleteConnection(id: string): Promise<void> {
  await apiClient.delete(`/workflows/connections/${id}`);
}
