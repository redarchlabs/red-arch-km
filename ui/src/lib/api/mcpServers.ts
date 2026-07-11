/** MCP servers — external MCP endpoints the org's agents can call tools on. */

import apiClient from "./client";

export type McpTransport = "stdio" | "http" | "sse";
export type McpAuthType = "none" | "bearer" | "api_key" | "oauth";
export type McpOAuthIdentity = "org" | "user";

export interface McpOAuthStatus {
  oauth: boolean;
  identity?: string | null;
  connected?: boolean | null;
  expires_at?: string | null;
}

export interface McpServer {
  id: string;
  name: string;
  description: string | null;
  transport: string;
  command: string | null;
  url: string | null;
  config: Record<string, unknown>;
  enabled: boolean;
  auth_type: string;
  has_secret: boolean;
  oauth_identity: string;
  oauth_status: McpOAuthStatus;
  created_at: string;
}

export interface McpServerCreateInput {
  name: string;
  description?: string | null;
  transport: McpTransport;
  command?: string | null;
  url?: string | null;
  config?: Record<string, unknown>;
  auth_type?: McpAuthType;
  secret?: string | null;
  enabled?: boolean;
  oauth_identity?: McpOAuthIdentity;
  oauth_client_id?: string | null;
  oauth_client_secret?: string | null;
  oauth_scopes?: string | null;
}

export interface McpToolInfo {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
}

export interface McpPreset {
  key: string;
  label: string;
  url: string;
  transport: string;
  auth_type: string;
  scopes: string | null;
  supports_dcr: boolean;
  notes: string;
}

export async function listMcpServers(): Promise<McpServer[]> {
  return (await apiClient.get<McpServer[]>("/agents/mcp-servers")).data;
}

export async function createMcpServer(input: McpServerCreateInput): Promise<McpServer> {
  return (await apiClient.post<McpServer>("/agents/mcp-servers", input)).data;
}

export async function deleteMcpServer(id: string): Promise<void> {
  await apiClient.delete(`/agents/mcp-servers/${id}`);
}

export async function testMcpServer(id: string): Promise<McpToolInfo[]> {
  return (await apiClient.post<McpToolInfo[]>(`/agents/mcp-servers/${id}/test`)).data;
}

export async function listMcpPresets(): Promise<McpPreset[]> {
  return (await apiClient.get<McpPreset[]>("/agents/mcp-servers/presets")).data;
}

export async function startMcpOAuth(id: string): Promise<{ authorization_url: string }> {
  return (await apiClient.post<{ authorization_url: string }>(`/agents/mcp-servers/${id}/oauth/start`)).data;
}

export async function disconnectMcpOAuth(id: string): Promise<void> {
  await apiClient.post(`/agents/mcp-servers/${id}/oauth/disconnect`);
}
