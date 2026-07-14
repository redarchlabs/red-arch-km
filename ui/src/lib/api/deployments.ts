/**
 * Site-admin cross-org deployment log: every config promotion across all
 * organizations. See services/api/src/api/routers/admin.py (/api/admin/deployments).
 */

import apiClient from "./client";
import type { PromotionStatus } from "./promotions";

export interface DeploymentRow {
  id: string;
  release_name: string | null;
  source_org: string | null;
  target_label: string;
  target_kind: string;
  target_org: string | null;
  status: PromotionStatus;
  strategy: string;
  is_rollback: boolean;
  created_at: string | null;
}

export async function listDeployments(): Promise<DeploymentRow[]> {
  return (await apiClient.get<DeploymentRow[]>("/admin/deployments")).data;
}
