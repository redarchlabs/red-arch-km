import apiClient from "./client";

/**
 * BPMN node categories (schema_version 2) plus the still-supported legacy types.
 * Mirrors the backend vocabulary in `services/workflow/constants.py`; a node's
 * concrete subtype (task_type / gateway_type / event position+type) lives in
 * `data`. Legacy graphs keep running, so their types remain valid here.
 */
export type NodeType =
  // BPMN categories
  | "trigger"
  | "task"
  | "gateway"
  | "event"
  // legacy (interpreted at read time by the backend, never rewritten)
  | "condition"
  | "action"
  | "switch"
  | "delay"
  | "merge"
  | "passthrough";

export interface GraphNode {
  id: string;
  type: NodeType;
  position: { x: number; y: number };
  data: Record<string, unknown>;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  source_handle?: string | null;
}

export interface WorkflowDefinition {
  schema_version?: number;
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export type VersionStatus = "draft" | "published" | "archived";

export type RunPermissionMode = "org_admin" | "any_member" | "roles";

export interface RunPermission {
  mode: RunPermissionMode;
  role_ids: string[];
  group_ids: string[];
}

export interface Workflow {
  id: string;
  name: string;
  description: string | null;
  entity_definition_id: string;
  enabled: boolean;
  active_version_id: string | null;
  run_permission: RunPermission;
}

export interface ManualRunResult {
  run_id: string;
  status: RunStatus;
  conditions_matched: boolean;
  actions_executed: number;
  error: string | null;
}

export interface WorkflowVersion {
  id: string;
  version_number: number;
  status: VersionStatus;
  definition: WorkflowDefinition;
  published_at: string | null;
}

export interface WorkflowTestResult {
  conditions_matched: boolean;
  error: string | null;
  condition_trace: { node_id: string; result: boolean }[];
  steps: { node_id: string; action_type: string; simulated_output: Record<string, unknown> }[];
}

export type RunStatus = "pending" | "running" | "waiting" | "succeeded" | "failed" | "skipped";

export interface WorkflowRun {
  id: string;
  workflow_id: string;
  workflow_version_id: string;
  trigger_operation: string;
  record_id: string | null;
  status: RunStatus;
  conditions_matched: boolean;
  error: string | null;
  depth: number;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  // Set when the run terminated with an uncaught error that exhausted retries and
  // hit no catcher — surfaced as a dead-letter/DLQ badge for manual replay.
  // Optional: only populated once the backend run schema serializes it.
  dead_letter?: boolean;
}

export interface WorkflowRunStep {
  id: string;
  node_id: string;
  action_type: string;
  step_index: number;
  status: string;
  attempts: number;
  max_attempts: number;
  next_retry_at: string | null;
  output: Record<string, unknown> | null;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export async function listWorkflows(): Promise<Workflow[]> {
  return (await apiClient.get<Workflow[]>("/workflows/")).data;
}

export async function getWorkflow(id: string): Promise<Workflow> {
  return (await apiClient.get<Workflow>(`/workflows/${id}`)).data;
}

export async function createWorkflow(input: {
  name: string;
  entity_definition_id: string;
  description?: string | null;
}): Promise<Workflow> {
  return (await apiClient.post<Workflow>("/workflows/", input)).data;
}

export async function updateWorkflow(
  id: string,
  input: {
    name?: string;
    description?: string | null;
    enabled?: boolean;
    run_permission?: RunPermission;
  },
): Promise<Workflow> {
  return (await apiClient.patch<Workflow>(`/workflows/${id}`, input)).data;
}

/** Run the published workflow for real against provided inputs. */
export async function runWorkflow(
  id: string,
  input: {
    operation: string;
    record_id?: string | null;
    before?: Record<string, unknown> | null;
    after?: Record<string, unknown> | null;
  },
): Promise<ManualRunResult> {
  return (await apiClient.post<ManualRunResult>(`/workflows/${id}/run`, input)).data;
}

export async function deleteWorkflow(id: string): Promise<void> {
  await apiClient.delete(`/workflows/${id}`);
}

export async function listVersions(id: string): Promise<WorkflowVersion[]> {
  return (await apiClient.get<WorkflowVersion[]>(`/workflows/${id}/versions`)).data;
}

export async function saveDraft(id: string, definition: WorkflowDefinition): Promise<WorkflowVersion> {
  return (await apiClient.post<WorkflowVersion>(`/workflows/${id}/versions`, { definition })).data;
}

export async function publishVersion(id: string, versionId: string): Promise<WorkflowVersion> {
  return (await apiClient.post<WorkflowVersion>(`/workflows/${id}/versions/${versionId}/publish`, {})).data;
}

export async function testVersion(
  id: string,
  versionId: string,
  input: { operation: string; before?: Record<string, unknown> | null; after?: Record<string, unknown> | null },
): Promise<WorkflowTestResult> {
  return (await apiClient.post<WorkflowTestResult>(`/workflows/${id}/versions/${versionId}/test`, input)).data;
}

export async function listRuns(id: string, limit = 50): Promise<WorkflowRun[]> {
  return (await apiClient.get<WorkflowRun[]>(`/workflows/${id}/runs`, { params: { limit } })).data;
}

export async function listRunSteps(runId: string): Promise<WorkflowRunStep[]> {
  return (await apiClient.get<WorkflowRunStep[]>(`/workflows/runs/${runId}/steps`)).data;
}
