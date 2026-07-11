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
