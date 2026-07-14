"use client";

import { useEffect, useState } from "react";

import { StatusBadge } from "@/components/change-management/parts";
import { listDeployments, type DeploymentRow } from "@/lib/api/deployments";
import { getApiErrorMessage } from "@/lib/api/errors";

export function DeploymentLogManager() {
  const [rows, setRows] = useState<DeploymentRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listDeployments()
      .then(setRows)
      .catch((e) => setError(getApiErrorMessage(e, "Could not load the deployment log.")));
  }, []);

  if (error) return <p className="text-sm text-destructive">{error}</p>;
  if (!rows) return <p className="text-sm text-muted-foreground">Loading…</p>;
  if (rows.length === 0) return <p className="text-sm text-muted-foreground">No promotions across any org yet.</p>;

  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">
        Every configuration promotion across all organizations on this instance, newest first.
      </p>
      <div className="overflow-x-auto rounded-md border">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b bg-muted/40 text-left text-muted-foreground">
              <th className="px-3 py-2 font-medium">When</th>
              <th className="px-3 py-2 font-medium">Release</th>
              <th className="px-3 py-2 font-medium">From</th>
              <th className="px-3 py-2 font-medium">To</th>
              <th className="px-3 py-2 font-medium">Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className="border-b last:border-0">
                <td className="whitespace-nowrap px-3 py-2 text-xs text-muted-foreground">
                  {r.created_at ? new Date(r.created_at).toLocaleString() : "—"}
                </td>
                <td className="px-3 py-2">
                  {r.release_name ?? "—"}
                  {r.is_rollback ? <span className="ml-1 text-xs text-muted-foreground">(rollback)</span> : null}
                </td>
                <td className="px-3 py-2">{r.source_org ?? "—"}</td>
                <td className="px-3 py-2">
                  {r.target_label}
                  {r.target_org ? <span className="ml-1 text-xs text-muted-foreground">→ {r.target_org}</span> : null}
                </td>
                <td className="px-3 py-2">
                  <StatusBadge status={r.status} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
