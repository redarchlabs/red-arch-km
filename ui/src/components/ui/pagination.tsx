"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface PaginationProps {
  /** 1-based current page. */
  page: number;
  /** Total number of pages (>= 1). */
  pageCount: number;
  /** Total item count, shown for context. */
  total: number;
  onPageChange: (page: number) => void;
  /** Singular noun for the item count label (e.g. "run" -> "12 runs"). */
  itemLabel?: string;
  className?: string;
}

/** Compact prev/next pager with a "Page X of Y · N total" label. Renders the
 * label alone when there's only one page (controls appear once paging matters). */
export function Pagination({
  page,
  pageCount,
  total,
  onPageChange,
  itemLabel = "item",
  className,
}: PaginationProps) {
  const label = `${total} ${itemLabel}${total === 1 ? "" : "s"}`;
  return (
    <div className={cn("flex items-center justify-between gap-2", className)}>
      <span className="text-xs text-muted-foreground">
        {pageCount > 1 ? `Page ${page} of ${pageCount} · ` : ""}
        {label}
      </span>
      {pageCount > 1 ? (
        <div className="flex items-center gap-1">
          <Button
            variant="outline"
            size="icon"
            className="h-7 w-7"
            disabled={page <= 1}
            onClick={() => onPageChange(page - 1)}
            aria-label="Previous page"
          >
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <Button
            variant="outline"
            size="icon"
            className="h-7 w-7"
            disabled={page >= pageCount}
            onClick={() => onPageChange(page + 1)}
            aria-label="Next page"
          >
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
      ) : null}
    </div>
  );
}
