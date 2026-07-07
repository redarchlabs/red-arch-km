/**
 * Site-admin console: Celery beat + queue (/api/admin/celery).
 */
import apiClient from "./client";

export interface CeleryQueueItem {
  task: string | null;
  id: string | null;
  eta: string | null;
  args: string | null;
  kwargs: string | null;
}

export interface BeatScheduleEntry {
  name: string;
  task: string | null;
  schedule_seconds: number | null;
}

export interface BeatStatus {
  status: "ok" | "stale" | "down";
  last_tick: string | null;
  age_seconds: number | null;
  detail: string | null;
}

export interface CeleryActiveTask {
  task: string | null;
  id: string | null;
  worker: string | null;
  args: string | null;
  kwargs: string | null;
  document_id: string | null;
  status: string | null;
  percent: number | null;
  stage: string | null;
}

export interface CeleryStatus {
  queue_name: string;
  depth: number;
  items: CeleryQueueItem[];
  truncated: boolean;
  beat: BeatStatus;
  schedule: BeatScheduleEntry[];
  active: CeleryActiveTask[];
}

export async function fetchCeleryStatus(): Promise<CeleryStatus> {
  const response = await apiClient.get<CeleryStatus>("/admin/celery");
  return response.data;
}

export interface JobLogEntry {
  ts: string | null;
  level: string | null;
  stage: string | null;
  message: string | null;
}

export interface JobLogsResponse {
  document_id: string;
  events: JobLogEntry[];
}

/** Ingest job log lines for a document (site-admin console drill-in). */
export async function fetchJobLogs(documentId: string): Promise<JobLogsResponse> {
  const response = await apiClient.get<JobLogsResponse>(`/admin/jobs/${documentId}/logs`);
  return response.data;
}

export interface JobCancelResult {
  document_id: string;
  status: string;
}

/** Cancel a document's ingest from the console (cross-org, site-admin only). */
export async function cancelJob(documentId: string): Promise<JobCancelResult> {
  const response = await apiClient.post<JobCancelResult>(`/admin/jobs/${documentId}/cancel`);
  return response.data;
}
