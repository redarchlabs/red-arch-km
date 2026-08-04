"use client";

import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useOrg } from "@/context/OrgContext";
import { getApiErrorMessage } from "@/lib/api/errors";
import { updateOrgSettings } from "@/lib/api/orgs";
import { listViews, type View } from "@/lib/api/views";

/**
 * Admin → General: org settings an org admin owns.
 *
 * Currently just the home view — the view members land on, and the target of
 * the sidebar "Home" item. It lives here rather than in Site Admin because it
 * points at a view this org authored; see PATCH /api/orgs/{id}/settings.
 */
export function GeneralSettingsManager() {
  const { currentOrg, currentOrgId, isOrgAdmin, refresh } = useOrg();
  const [views, setViews] = useState<View[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  // "" means no home view; otherwise a view id.
  const [homeViewId, setHomeViewId] = useState("");

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      setViews(await listViews());
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to load views"));
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Re-seed the selector whenever the org (or its stored home view) changes, so
  // the control always starts from what is actually persisted.
  useEffect(() => {
    setHomeViewId(currentOrg?.home_view_id ?? "");
    setSaved(false);
  }, [currentOrg?.id, currentOrg?.home_view_id]);

  const persisted = currentOrg?.home_view_id ?? "";
  const isDirty = homeViewId !== persisted;

  const handleSave = async () => {
    if (!currentOrgId || !isDirty || isSaving) return;
    setIsSaving(true);
    setError(null);
    setSaved(false);
    try {
      await updateOrgSettings(currentOrgId, { home_view_id: homeViewId || null });
      // Refresh the org context so the sidebar "Home" item points at the new
      // view immediately instead of after the next full page load.
      await refresh();
      setSaved(true);
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Save failed"));
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <Card>
      <CardContent className="space-y-4 pt-6">
        <div>
          <h2 className="text-lg font-semibold">General</h2>
          <p className="text-sm text-muted-foreground">
            Settings for {currentOrg?.name ?? "this organization"}.
          </p>
        </div>

        {error ? <p className="text-sm text-destructive">{error}</p> : null}

        {isLoading ? (
          <Skeleton className="h-10 w-full max-w-sm" />
        ) : (
          <div className="space-y-2">
            <label className="block text-sm font-medium" htmlFor="home-view">
              Home view
            </label>
            <select
              id="home-view"
              className="h-9 w-full max-w-sm rounded-md border bg-background px-2 text-sm text-foreground disabled:opacity-60"
              value={homeViewId}
              disabled={!isOrgAdmin || isSaving}
              onChange={(e) => {
                setHomeViewId(e.target.value);
                setSaved(false);
              }}
            >
              <option value="">(none)</option>
              {views.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.name}
                </option>
              ))}
            </select>
            <p className="text-sm text-muted-foreground">
              The view members land on, and where the sidebar <strong>Home</strong> item goes.
              Choose <em>(none)</em> to send them to the default landing page instead.
            </p>
            {isOrgAdmin ? (
              <div className="flex items-center gap-3">
                <Button onClick={() => void handleSave()} disabled={!isDirty || isSaving}>
                  {isSaving ? "Saving…" : "Save"}
                </Button>
                {saved && !isDirty ? (
                  <span className="text-sm text-muted-foreground">Saved.</span>
                ) : null}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">
                Only organization admins can change this.
              </p>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
