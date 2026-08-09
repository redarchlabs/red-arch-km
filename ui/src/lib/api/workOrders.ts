/** Work orders — the durable unit of work an agent org executes. */

import apiClient from "./client";

export type WorkOrderStatus =
  | "draft"
  | "awaiting_approval"
  | "approved"
  | "in_progress"
  | "done"
  | "cancelled";

export interface WorkOrder {
  id: string;
  slug: string;
  title: string;
  status: string;
  body: string | null;
  priority: string;
  assigned_agent_id: string | null;
  created_by_profile_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface WorkOrderTask {
  id: string;
  key: string;
  title: string;
  status: string;
  sort_order: number;
  assigned_agent_id: string | null;
}

export interface WorkOrderEntry {
  id: string;
  agent_id: string | null;
  agent_run_id: string | null;
  role: string | null;
  text: string;
  created_at: string;
}

export interface WorkOrderDetail extends WorkOrder {
  tasks: WorkOrderTask[];
  entries: WorkOrderEntry[];
  progress: number;
}

/** One participant's horizontal track. `key` is the agent id, or "human" for the
 *  lane that questions and approvals land in. */
export interface WorkOrderMapLane {
  key: string;
  label: string;
  avatar: string | null;
  agent_kind: string | null;
  status: string | null;
}

export type WorkOrderEventKind =
  | "started"
  | "delegated"
  | "consulted"
  | "answered"
  | "blocked"
  | "finished"
  | "failed"
  | "note";

export interface WorkOrderMapEvent {
  id: string;
  lane: string;
  kind: WorkOrderEventKind;
  at: string;
  title: string;
  detail: string | null;
  /** Where a cross-lane arrow lands: the consulted peer, or the human being asked. */
  target_lane: string | null;
  run_id: string | null;
}

export interface WorkOrderMap {
  lanes: WorkOrderMapLane[];
  events: WorkOrderMapEvent[];
}

export interface WorkOrderCreateInput {
  title: string;
  body?: string | null;
  priority?: string;
  assigned_agent_id?: string | null;
}

export async function listWorkOrders(): Promise<WorkOrder[]> {
  return (await apiClient.get<WorkOrder[]>("/work-orders/")).data;
}

export async function getWorkOrder(id: string): Promise<WorkOrderDetail> {
  return (await apiClient.get<WorkOrderDetail>(`/work-orders/${id}`)).data;
}

export async function getWorkOrderMap(id: string): Promise<WorkOrderMap> {
  return (await apiClient.get<WorkOrderMap>(`/work-orders/${id}/map`)).data;
}

export interface WorkOrderEntryPage {
  entries: WorkOrderEntry[];
  has_more: boolean;
}

/** A page of diary, oldest-first. `before` is the id of the oldest entry already
 *  held, so scrolling up walks backwards without an offset that would repeat or
 *  skip rows as agents keep writing. */
export async function getWorkOrderEntries(
  id: string,
  params: { limit?: number; before?: string } = {},
): Promise<WorkOrderEntryPage> {
  return (await apiClient.get<WorkOrderEntryPage>(`/work-orders/${id}/entries`, { params })).data;
}

export async function createWorkOrder(input: WorkOrderCreateInput): Promise<WorkOrder> {
  return (await apiClient.post<WorkOrder>("/work-orders/", input)).data;
}

export async function setWorkOrderStatus(id: string, status: WorkOrderStatus): Promise<WorkOrder> {
  return (await apiClient.patch<WorkOrder>(`/work-orders/${id}/status`, { status })).data;
}

export async function assignWorkOrder(id: string, assignedAgentId: string | null): Promise<WorkOrder> {
  return (await apiClient.patch<WorkOrder>(`/work-orders/${id}/assignment`, {
    assigned_agent_id: assignedAgentId,
  })).data;
}
