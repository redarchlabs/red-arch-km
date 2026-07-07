"use client";

import { ChevronDown, ChevronRight } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { getDocumentLogs, type JobLogEntry } from "@/lib/api/documents";

interface JobLogsProps {
  documentId: string;
  /** Poll for new lines while the ingest is still running. */
  poll?: boolean;
}

function formatTime(ts: string | null): string {
  if (!ts) return "";
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString();
}

function levelClass(level: string | null): string {
  if (level === "error") return "text-destructive";
  if (level === "warning") return "text-amber-600 dark:text-amber-500";
  return "text-foreground";
}

/**
 * The ingest job's log lines for a document, appended by the worker as it runs
 * each stage. Renders nothing until at least one line exists (so a doc with no
 * job history shows no empty card). Polls while `poll` is set.
 */
export function JobLogs({ documentId, poll = false }: JobLogsProps) {
  const [events, setEvents] = useState<JobLogEntry[]>([]);
  const [loaded, setLoaded] = useState(false);
  // Collapsed by default — the log is a diagnostic detail, not the main content.
  const [open, setOpen] = useState(false);

  const load = useCallback(async () => {
    try {
      const res = await getDocumentLogs(documentId);
      setEvents(res.events);
    } catch {
      // Non-fatal: a broker hiccup just shows no new lines.
    } finally {
      setLoaded(true);
    }
  }, [documentId]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!poll) return;
    const timer = setInterval(() => void load(), 4000);
    return () => clearInterval(timer);
  }, [poll, load]);

  if (loaded && events.length === 0) return null;

  return (
    <Card>
      <CardHeader className="pb-0">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className="flex w-full items-center gap-2 text-base font-medium text-muted-foreground hover:text-foreground"
        >
          {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
          Ingest log
          <span className="text-sm font-normal">({events.length})</span>
        </button>
      </CardHeader>
      {open ? (
        <CardContent className="pt-4">
          <ol className="max-h-72 space-y-1 overflow-y-auto font-mono text-xs">
            {events.map((e, i) => (
              <li key={`${e.ts ?? ""}-${i}`} className="flex gap-2">
                <span className="shrink-0 text-muted-foreground">{formatTime(e.ts)}</span>
                <span className={levelClass(e.level)}>{e.message}</span>
              </li>
            ))}
          </ol>
        </CardContent>
      ) : null}
    </Card>
  );
}
