"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useOrg } from "@/context/OrgContext";
import { getApiErrorMessage } from "@/lib/api/errors";
import { deleteOrgLogo, updateOrgSettings, uploadOrgLogo } from "@/lib/api/orgs";
import { listViews, type View } from "@/lib/api/views";

/** Logos are <img> sources, not axios calls, so they need the absolute API base. */
const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

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
  // "" means no accent (fall back to the theme's own primary).
  const [accent, setAccent] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  // Bumped after an upload so the <img> re-fetches — the logo URL is stable and
  // cached hard (deliberately, for kiosks), so a replaced logo needs a cache bust.
  const [logoVersion, setLogoVersion] = useState(0);

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
    setAccent(currentOrg?.accent_color ?? "");
    setSaved(false);
  }, [currentOrg?.id, currentOrg?.home_view_id, currentOrg?.accent_color]);

  const persisted = currentOrg?.home_view_id ?? "";
  const isDirty = homeViewId !== persisted;
  const accentDirty = accent !== (currentOrg?.accent_color ?? "");

  const handleSaveAccent = async () => {
    if (!currentOrgId || isSaving) return;
    setIsSaving(true);
    setError(null);
    try {
      // Only accent_color is sent: the endpoint patches per field, so this
      // cannot disturb the home view the section above owns.
      await updateOrgSettings(currentOrgId, { accent_color: accent || null });
      await refresh();
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Save failed"));
    } finally {
      setIsSaving(false);
    }
  };

  const handleLogoUpload = async (file: File) => {
    if (!currentOrgId) return;
    setIsSaving(true);
    setError(null);
    try {
      await uploadOrgLogo(currentOrgId, file);
      await refresh();
      setLogoVersion((v) => v + 1);
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Logo upload failed"));
    } finally {
      setIsSaving(false);
    }
  };

  const handleLogoDelete = async () => {
    if (!currentOrgId) return;
    setIsSaving(true);
    setError(null);
    try {
      await deleteOrgLogo(currentOrgId);
      await refresh();
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Couldn't remove the logo"));
    } finally {
      setIsSaving(false);
    }
  };

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

        <div className="space-y-3 border-t pt-4">
          <div>
            <h3 className="text-sm font-semibold">Branding</h3>
            <p className="text-sm text-muted-foreground">
              Shown on the chrome-free view pages — a kiosk screen, and a shared link if
              you turn branding on for that link. Everywhere else uses the app&apos;s own theme.
            </p>
          </div>

          <div className="flex flex-wrap items-end gap-4">
            <div className="space-y-1.5">
              <label className="block text-sm font-medium" htmlFor="accent">
                Accent color
              </label>
              <div className="flex items-center gap-2">
                <input
                  id="accent"
                  type="color"
                  className="h-9 w-14 cursor-pointer rounded-md border bg-background disabled:opacity-60"
                  value={accent || "#c2410c"}
                  disabled={!isOrgAdmin || isSaving}
                  onChange={(e) => {
                    setAccent(e.target.value);
                    setSaved(false);
                  }}
                />
                {accent ? (
                  <button
                    type="button"
                    className="text-xs text-muted-foreground underline-offset-2 hover:underline"
                    disabled={!isOrgAdmin || isSaving}
                    onClick={() => {
                      setAccent("");
                      setSaved(false);
                    }}
                  >
                    Clear
                  </button>
                ) : null}
              </div>
            </div>

            <div className="space-y-1.5">
              <span className="block text-sm font-medium">Logo</span>
              <div className="flex items-center gap-3">
                {currentOrg?.has_logo ? (
                  // eslint-disable-next-line @next/next/no-img-element -- API-served
                  <img
                    src={`${API_BASE}/orgs/${currentOrgId}/settings/logo?v=${logoVersion}`}
                    alt="Organization logo"
                    className="h-9 w-auto max-w-32 object-contain"
                  />
                ) : (
                  <span className="text-sm text-muted-foreground">None uploaded</span>
                )}
                <input
                  ref={fileRef}
                  type="file"
                  accept="image/png,image/jpeg,image/webp"
                  className="hidden"
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) void handleLogoUpload(f);
                    e.target.value = "";
                  }}
                />
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={!isOrgAdmin || isSaving}
                  onClick={() => fileRef.current?.click()}
                >
                  {currentOrg?.has_logo ? "Replace" : "Upload"}
                </Button>
                {currentOrg?.has_logo ? (
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    disabled={!isOrgAdmin || isSaving}
                    onClick={() => void handleLogoDelete()}
                  >
                    Remove
                  </Button>
                ) : null}
              </div>
            </div>
          </div>

          <p className="text-xs text-muted-foreground">PNG, JPEG or WebP, up to 2MB.</p>

          {isOrgAdmin && accentDirty ? (
            <Button onClick={() => void handleSaveAccent()} disabled={isSaving}>
              {isSaving ? "Saving…" : "Save accent"}
            </Button>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}
