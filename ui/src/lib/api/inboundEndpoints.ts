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
  /** Whether callers must send a valid HMAC signature (the secret is never returned on reads). */
  has_signing_secret: boolean;
}

/**
 * The create response — carries the one-time plaintext token + callable URL AND
 * the one-time HMAC signing secret. The caller must sign each request:
 * `signature_header: t=<unix>,v1=<hex hmac_sha256(secret, `${t}.${rawBody}`)>`.
 */
export interface InboundEndpointCreated extends InboundEndpoint {
  token: string;
  url: string;
  signing_secret: string;
  signature_header: string;
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
