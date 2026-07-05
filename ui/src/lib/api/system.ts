/**
 * Site-admin console: system status (/api/admin/system).
 */
import apiClient from "./client";

export interface ComponentStatus {
  status: "ok" | "error";
  latency_ms: number | null;
  detail: string | null;
}

export interface SystemStatus {
  version: string;
  components: Record<string, ComponentStatus>;
}

export async function fetchSystemStatus(): Promise<SystemStatus> {
  const response = await apiClient.get<SystemStatus>("/admin/system");
  return response.data;
}
