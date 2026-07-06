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

export interface CeleryStatus {
  queue_name: string;
  depth: number;
  items: CeleryQueueItem[];
  truncated: boolean;
  beat: BeatStatus;
  schedule: BeatScheduleEntry[];
}

export async function fetchCeleryStatus(): Promise<CeleryStatus> {
  const response = await apiClient.get<CeleryStatus>("/admin/celery");
  return response.data;
}
