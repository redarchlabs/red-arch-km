import { isAxiosError } from "axios";

/**
 * Extract a user-facing message from an API error, preferring the backend's
 * `detail` field (FastAPI convention) over axios's generic message.
 */
export function getApiErrorMessage(error: unknown, fallback: string): string {
  if (isAxiosError(error)) {
    const detail: unknown = error.response?.data?.detail;
    if (typeof detail === "string" && detail.length > 0) {
      return detail;
    }
  }
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return fallback;
}

/** HTTP status of an API error, or null when unavailable. */
export function getApiErrorStatus(error: unknown): number | null {
  return isAxiosError(error) ? (error.response?.status ?? null) : null;
}
