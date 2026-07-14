/**
 * Change-management control plane: promotion targets, releases (frozen config
 * snapshots), governance (submit/approve/reject), promote/rollback, and history.
 * See services/api/src/api/routers/promotions.py.
 */

import apiClient from "./client";
import type { BundleDiff, CollisionStrategy, GeneratedSecret, Selection } from "./migration";

export type TargetKind = "local_org" | "remote_instance";
export type ReleaseStatus = "draft" | "in_review" | "approved" | "rejected" | "archived";
export type PromotionStatus = "pending" | "promoting" | "promoted" | "failed" | "rolled_back";

export interface PromotionTarget {
  id: string;
  name: string;
  kind: TargetKind;
  enabled: boolean;
  target_org_id: string | null;
  base_url: string | null;
  remote_org_id: string | null;
  has_key: boolean;
  config: Record<string, unknown>;
}

export interface Release {
  id: string;
  name: string;
  description: string | null;
  status: ReleaseStatus;
  bundle_hash: string | null;
  bundle_format_version: number | null;
  created_by_id: string | null;
}

export interface ReleaseItem {
  object_type: string;
  lineage_id: string;
  natural_key: string | null;
}

export interface Approval {
  id: string;
  approver_id: string | null;
  decision: "approved" | "rejected";
  comment: string | null;
}

export interface Promotion {
  id: string;
  release_id: string;
  target_id: string | null;
  target_kind: TargetKind;
  target_label: string;
  target_org_id: string | null;
  status: PromotionStatus;
  strategy: CollisionStrategy;
  promoted_by_id: string | null;
  result_summary: Record<string, unknown> | null;
  rollback_source_id: string | null;
}

export interface ReleaseDetail {
  release: Release;
  items: ReleaseItem[];
  approvals: Approval[];
  promotions: Promotion[];
}

export interface PromoteResponse {
  promotion: Promotion;
  diff: BundleDiff;
  generated_secrets: GeneratedSecret[];
}

/** A live-run dependency that blocks a destructive promotion (409 detail). */
export interface InFlightBlocker {
  resource_type: string;
  lineage_id: string;
  name: string;
  run_count: number;
  reason: string;
}

// --- Targets --------------------------------------------------------------- //
export async function listTargets(): Promise<PromotionTarget[]> {
  return (await apiClient.get<PromotionTarget[]>("/promotions/targets")).data;
}

export interface TargetCreate {
  name: string;
  kind: TargetKind;
  target_org_id?: string | null;
  base_url?: string | null;
  remote_org_id?: string | null;
  api_key?: string | null;
  config?: Record<string, unknown> | null;
}

export async function createTarget(body: TargetCreate): Promise<PromotionTarget> {
  return (await apiClient.post<PromotionTarget>("/promotions/targets", body)).data;
}

export async function deleteTarget(id: string): Promise<void> {
  await apiClient.delete(`/promotions/targets/${id}`);
}

export interface TargetTestResult {
  ok: boolean;
  remote_bundle_format_version: number | null;
  error: string | null;
}

export async function testTarget(id: string): Promise<TargetTestResult> {
  return (await apiClient.post<TargetTestResult>(`/promotions/targets/${id}/test`, {}, { timeout: 30000 })).data;
}

// --- Releases -------------------------------------------------------------- //
export async function listReleases(): Promise<Release[]> {
  return (await apiClient.get<Release[]>("/promotions/releases")).data;
}

export async function createRelease(body: {
  name: string;
  description?: string | null;
  selection?: Selection | null;
}): Promise<Release> {
  return (await apiClient.post<Release>("/promotions/releases", body)).data;
}

export async function getRelease(id: string): Promise<ReleaseDetail> {
  return (await apiClient.get<ReleaseDetail>(`/promotions/releases/${id}`)).data;
}

export async function submitRelease(id: string): Promise<Release> {
  return (await apiClient.post<Release>(`/promotions/releases/${id}/submit`, {})).data;
}

export async function approveRelease(id: string, comment?: string): Promise<Release> {
  return (await apiClient.post<Release>(`/promotions/releases/${id}/approve`, { comment })).data;
}

export async function rejectRelease(id: string, comment?: string): Promise<Release> {
  return (await apiClient.post<Release>(`/promotions/releases/${id}/reject`, { comment })).data;
}

// --- Diff / Promote / Rollback --------------------------------------------- //
export async function diffRelease(
  id: string,
  body: { target_id: string; strategy?: CollisionStrategy; apply_deletes?: boolean },
): Promise<BundleDiff> {
  return (await apiClient.post<BundleDiff>(`/promotions/releases/${id}/diff`, body)).data;
}

export async function promoteRelease(
  id: string,
  body: {
    target_id: string;
    strategy?: CollisionStrategy;
    apply_deletes?: boolean;
    allow_data?: boolean;
    override_inflight?: boolean;
  },
): Promise<PromoteResponse> {
  return (await apiClient.post<PromoteResponse>(`/promotions/releases/${id}/promote`, body, { timeout: 300000 }))
    .data;
}

export async function listPromotions(releaseId?: string): Promise<Promotion[]> {
  return (await apiClient.get<Promotion[]>("/promotions", { params: { release_id: releaseId } })).data;
}

export async function rollbackPromotion(id: string): Promise<Promotion> {
  return (await apiClient.post<Promotion>(`/promotions/${id}/rollback`, {}, { timeout: 300000 })).data;
}
