import apiClient from "./client";

export interface SetupStatus {
  needs_setup: boolean;
}

export async function fetchSetupStatus(): Promise<SetupStatus> {
  const response = await apiClient.get<SetupStatus>("/setup/status");
  return response.data;
}

export async function claimSetup(token: string): Promise<{ claimed: boolean }> {
  const response = await apiClient.post<{ claimed: boolean }>("/setup/claim", { token });
  return response.data;
}
