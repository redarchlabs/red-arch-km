/** Work orders — the durable unit of work an agent org executes. */

import apiClient from "./client";

export type WorkOrderStatus =
  | "draft"
  | "awaiting_approval"
  | "approved"
  | "in_progress"
  | "done"
  | "cancelled";

/** How much rope the agent gets on one order: plan = think but change nothing;
 *  manual = pause for approval; automatic = approve its own actions. */
export type WorkOrderMode = "plan" | "manual" | "automatic";

/** How big a board reviews this order's plan before a person is asked to approve
 *  it: none | light (1) | standard (2) | full (4). */
export type WorkOrderReviewLevel = "none" | "light" | "standard" | "full";

export interface WorkOrder {
  id: string;
  slug: string;
  title: string;
  status: string;
  body: string | null;
  priority: string;
  mode: WorkOrderMode;
  review_level: WorkOrderReviewLevel;
  assigned_agent_id: string | null;
  created_by_profile_id: string | null;
  created_at: string;
  updated_at: string;
  /** Statuses this order may move to next, from the server's own state machine —
   *  so the buttons shown can never offer a move the server would reject. */
  allowed_transitions?: string[];
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
  mode?: WorkOrderMode;
  review_level?: WorkOrderReviewLevel;
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

/** Reply to the agent working an order. A finished run cannot be answered, so a
 *  reply records the message and queues a follow-up run carrying the diary. */
export async function replyToWorkOrder(id: string, text: string): Promise<WorkOrder> {
  return (await apiClient.post<WorkOrder>(`/work-orders/${id}/reply`, { text })).data;
}

/** Change how much rope the agent gets on this order. */
export async function setWorkOrderMode(id: string, mode: WorkOrderMode): Promise<WorkOrder> {
  return (await apiClient.patch<WorkOrder>(`/work-orders/${id}/mode`, { mode })).data;
}

/** Change how big a board reviews this order. */
export async function setWorkOrderReviewLevel(
  id: string,
  level: WorkOrderReviewLevel,
): Promise<WorkOrder> {
  return (await apiClient.patch<WorkOrder>(`/work-orders/${id}/review-level`, { review_level: level })).data;
}
