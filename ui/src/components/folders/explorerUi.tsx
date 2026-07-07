"use client";

import { FileText, Folder as FolderIcon, Loader2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { Document, ProcessingDetails } from "@/types";

import type { ExplorerItem } from "./explorerItem";

/**
 * Compact status badge shown in the file browser; nothing once ready. While
 * processing it surfaces the coarse ingest percent (from processing_details)
 * so the explorer shows live progress, not just a spinner.
 */
export function StatusBadge({
  status,
  details,
}: {
  status: Document["processing_status"];
  details?: ProcessingDetails | null;
}) {
  if (status === "SUCCESS") return null;
  if (status === "FAILED") return <Badge variant="destructive">Failed</Badge>;
  if (status === "CANCELLED") return <Badge variant="outline">Cancelled</Badge>;
  const percent = typeof details?.percent === "number" ? Math.round(details.percent) : null;
  return (
    <Badge variant="secondary" className="gap-1">
      <Loader2 className="h-3 w-3 animate-spin" />
      {percent != null ? `Processing ${percent}%` : "Processing"}
    </Badge>
  );
}

export function ItemIcon({ item, className }: { item: ExplorerItem; className?: string }) {
  return item.kind === "folder" ? (
    <FolderIcon className={cn("text-muted-foreground", className)} />
  ) : (
    <FileText className={cn("text-muted-foreground", className)} />
  );
}

/** Track a container's pixel size so the virtualizer can fill it exactly. */
export function useElementSize() {
  const ref = useRef<HTMLDivElement | null>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => {
      const rect = entry?.contentRect;
      if (rect) setSize({ width: rect.width, height: rect.height });
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return { ref, ...size };
}
