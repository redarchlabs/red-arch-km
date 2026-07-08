import { cn } from "@/lib/utils";

/** Tailwind classes per run/step status — shared by the run monitor and the
 * org-wide activity feed so a status reads the same everywhere. */
export const STATUS_CLASSES: Record<string, string> = {
  succeeded: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
  failed: "bg-rose-500/15 text-rose-600 dark:text-rose-400",
  running: "bg-sky-500/15 text-sky-600 dark:text-sky-400",
  waiting: "bg-violet-500/15 text-violet-600 dark:text-violet-400",
  retrying: "bg-amber-500/15 text-amber-600 dark:text-amber-400",
  skipped: "bg-amber-500/15 text-amber-600 dark:text-amber-400",
  pending: "bg-muted text-muted-foreground",
};

/** Statuses for a run/step that may still change — used to decide whether to poll. */
export const ACTIVE_STATUSES = new Set(["pending", "running", "retrying", "waiting"]);

export function StatusPill({ status }: { status: string }) {
  return (
    <span className={cn("rounded-full px-2 py-0.5 text-xs font-medium", STATUS_CLASSES[status] ?? "bg-muted")}>
      {status}
    </span>
  );
}

/** Human-readable elapsed time between two ISO timestamps (or "—" if unknown). */
export function duration(start: string | null, end: string | null): string {
  if (!start || !end) return "—";
  const ms = new Date(end).getTime() - new Date(start).getTime();
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}
