"use client";

import { FileText, Folder as FolderIcon, Loader2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { Document } from "@/types";

import type { ExplorerItem } from "./explorerItem";

/** Compact status badge shown in the file browser; nothing once ready. */
export function StatusBadge({ status }: { status: Document["processing_status"] }) {
  if (status === "SUCCESS") return null;
  if (status === "FAILED") return <Badge variant="destructive">Failed</Badge>;
  return (
    <Badge variant="secondary" className="gap-1">
      <Loader2 className="h-3 w-3 animate-spin" />
      Processing
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
