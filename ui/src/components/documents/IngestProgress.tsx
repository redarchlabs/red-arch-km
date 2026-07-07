import type { Document, ProcessingDetails } from "@/types";

// Worker stage keys (worker/tasks/_job.py STAGE_*) → human labels.
const STAGE_LABEL: Record<string, string> = {
  queued: "Queued",
  downloading: "Downloading",
  extracting: "Extracting text",
  ingesting: "Indexing",
  done: "Done",
  cancelled: "Cancelled",
};

interface IngestProgressProps {
  status: Document["processing_status"];
  details: ProcessingDetails | null;
  className?: string;
}

/**
 * Coarse ingest progress bar. Renders only while a document is PENDING /
 * PROCESSING; terminal states show nothing (the status badge conveys those).
 * Percent + stage come from `processing_details`, which the worker updates at
 * each stage boundary (granularity is worker stages, not per-chunk).
 */
export function IngestProgress({ status, details, className }: IngestProgressProps) {
  if (status !== "PENDING" && status !== "PROCESSING") return null;

  const rawPercent = typeof details?.percent === "number" ? details.percent : status === "PENDING" ? 0 : 5;
  const percent = Math.min(100, Math.max(0, Math.round(rawPercent)));
  const stageKey = details?.stage;
  const label = stageKey ? (STAGE_LABEL[stageKey] ?? stageKey) : status === "PENDING" ? "Queued" : "Processing";

  return (
    <div className={className}>
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>{label}</span>
        <span aria-hidden>{percent}%</span>
      </div>
      <div
        className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-muted"
        role="progressbar"
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={`Ingest ${label.toLowerCase()}`}
      >
        <div className="h-full rounded-full bg-primary transition-all" style={{ width: `${percent}%` }} />
      </div>
    </div>
  );
}
