/**
 * Inbound webhook endpoints (org-admin) — public URLs that start a workflow run
 * when called. Mirrors the backend contract in
 * `services/api/src/api/routers/workflows.py` + `schemas/workflow.py`.
 *
 * The plaintext `token` (and the callable `url` embedding it) is returned ONCE on
 * creation; only its hash is stored. The UI must surface it immediately and warn
 * that it cannot be shown again.
 */
import apiClient from "./client";

export interface InboundEndpoint {
  id: string;
  name: string;
  workflow_id: string;
  enabled: boolean;
}

/** The create response — carries the one-time plaintext token + callable URL. */
export interface InboundEndpointCreated extends InboundEndpoint {
  token: string;
  url: string;
}

export interface InboundEndpointCreateInput {
  name: string;
  workflow_id: string;
}

export async function listInboundEndpoints(): Promise<InboundEndpoint[]> {
  return (await apiClient.get<InboundEndpoint[]>("/workflows/inbound-endpoints")).data;
}

export async function createInboundEndpoint(
  input: InboundEndpointCreateInput,
): Promise<InboundEndpointCreated> {
  return (await apiClient.post<InboundEndpointCreated>("/workflows/inbound-endpoints", input)).data;
}

export async function deleteInboundEndpoint(id: string): Promise<void> {
  await apiClient.delete(`/workflows/inbound-endpoints/${id}`);
}
