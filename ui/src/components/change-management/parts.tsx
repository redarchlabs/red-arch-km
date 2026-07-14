"use client";

import type { BundleDiff } from "@/lib/api/migration";
import type { PromotionStatus, ReleaseStatus } from "@/lib/api/promotions";
import { cn } from "@/lib/utils";

const RELEASE_BADGE: Record<ReleaseStatus, string> = {
  draft: "bg-slate-500/15 text-slate-600 dark:text-slate-300",
  in_review: "bg-amber-500/15 text-amber-600 dark:text-amber-400",
  approved: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
  rejected: "bg-destructive/15 text-destructive",
  archived: "bg-slate-500/15 text-slate-500 line-through",
};

const PROMOTION_BADGE: Record<PromotionStatus, string> = {
  pending: "bg-slate-500/15 text-slate-500",
  promoting: "bg-sky-500/15 text-sky-600 dark:text-sky-400",
  promoted: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
  failed: "bg-destructive/15 text-destructive",
  rolled_back: "bg-slate-500/15 text-slate-500 line-through",
};

export function StatusBadge({ status }: { status: ReleaseStatus | PromotionStatus }) {
  const cls =
    (RELEASE_BADGE as Record<string, string>)[status] ??
    (PROMOTION_BADGE as Record<string, string>)[status] ??
    "bg-slate-500/15 text-slate-500";
  return (
    <span className={cn("rounded-full px-2 py-0.5 text-xs font-medium capitalize", cls)}>
      {status.replace(/_/g, " ")}
    </span>
  );
}

const DIFF_COLUMNS = ["added", "changed", "deleted", "unchanged"] as const;

/** The added/changed/deleted/unchanged table, shared by preview and promote. */
export function DiffTable({ diff }: { diff: BundleDiff }) {
  const rows = diff.resources.filter((r) => r.added + r.changed + r.deleted + r.unchanged > 0);
  if (rows.length === 0) {
    return <p className="text-sm text-muted-foreground">The target already matches this release exactly.</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left text-muted-foreground">
            <th className="py-1 pr-4 font-medium">Resource</th>
            {DIFF_COLUMNS.map((k) => (
              <th key={k} className="px-2 py-1 text-right font-medium capitalize">
                {k}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.resource_type} className="border-b last:border-0">
              <td className="py-1 pr-4 font-medium capitalize">
                {r.resource_type.replace(/_/g, " ")}
                {r.count_only ? (
                  <span className="ml-1 text-xs font-normal text-muted-foreground">(data · count only)</span>
                ) : null}
              </td>
              {DIFF_COLUMNS.map((k) => (
                <td
                  key={k}
                  className={cn(
                    "px-2 py-1 text-right tabular-nums",
                    k === "deleted" && r.deleted > 0 ? "font-medium text-destructive" : "",
                    k === "added" && r.added > 0 ? "text-emerald-600 dark:text-emerald-400" : "",
                    r[k] === 0 ? "text-muted-foreground/50" : "",
                  )}
                >
                  {r[k]}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
