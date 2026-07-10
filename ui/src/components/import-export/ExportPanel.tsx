"use client";

import { Download } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { exportOrg, fetchManifest, type Selection } from "@/lib/api/migration";
import { getApiErrorMessage } from "@/lib/api/errors";

import { ResourceSelector } from "./ResourceSelector";
import { countSelected, manifestToGroups, selectAll, type SelectableGroup } from "./selection";

/** Trigger a browser download for an in-memory blob. */
function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export function ExportPanel() {
  const [groups, setGroups] = useState<SelectableGroup[] | null>(null);
  const [selection, setSelection] = useState<Selection>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const manifest = await fetchManifest();
      const next = manifestToGroups(manifest);
      setGroups(next);
      setSelection(selectAll(next));
    } catch (e) {
      setError(getApiErrorMessage(e, "Could not load this organization's objects."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleExport() {
    setBusy(true);
    try {
      const { blob, filename } = await exportOrg({ selection });
      downloadBlob(blob, filename);
      toast.success("Export ready", { description: `Downloaded ${filename}` });
    } catch (e) {
      toast.error("Export failed", { description: getApiErrorMessage(e, "Could not export this organization.") });
    } finally {
      setBusy(false);
    }
  }

  const selected = countSelected(selection);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Export this organization</CardTitle>
        <CardDescription>
          Choose exactly which objects to include, then download them as a portable JSON bundle. Records
          are grouped by entity; re-importing documents re-ingests them. Secrets (connection credentials,
          webhook tokens) are never included.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        {loading ? (
          <Skeleton className="h-64 w-full" />
        ) : error ? (
          <div className="space-y-3">
            <p className="text-sm text-destructive">{error}</p>
            <Button variant="outline" onClick={() => void load()}>
              Retry
            </Button>
          </div>
        ) : groups && groups.length > 0 ? (
          <>
            <ResourceSelector groups={groups} selection={selection} onChange={setSelection} />
            <div className="flex items-center gap-3">
              <Button onClick={handleExport} disabled={busy || selected === 0}>
                <Download className="mr-2 h-4 w-4" />
                {busy ? "Preparing export…" : `Export ${selected} object${selected === 1 ? "" : "s"}`}
              </Button>
              {selected === 0 ? (
                <span className="text-sm text-muted-foreground">Select at least one object.</span>
              ) : null}
            </div>
          </>
        ) : (
          <p className="text-sm text-muted-foreground">This organization has nothing to export yet.</p>
        )}
      </CardContent>
    </Card>
  );
}
