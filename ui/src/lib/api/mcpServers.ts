/** MCP servers — external MCP endpoints the org's agents can call tools on. */

import apiClient from "./client";

export type McpTransport = "stdio" | "http" | "sse";

export interface McpServer {
  id: string;
  name: string;
  description: string | null;
  transport: string;
  command: string | null;
  url: string | null;
  config: Record<string, unknown>;
  enabled: boolean;
  has_secret: boolean;
  created_at: string;
}

export interface McpServerCreateInput {
  name: string;
  description?: string | null;
  transport: McpTransport;
  command?: string | null;
  url?: string | null;
  config?: Record<string, unknown>;
  secret?: string | null;
  enabled?: boolean;
}

export interface McpToolInfo {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
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
