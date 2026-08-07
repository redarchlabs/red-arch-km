"use client";

import { Check, Copy, Globe, Link2Off, RotateCw, TriangleAlert } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { getApiErrorMessage } from "@/lib/api/errors";
import { disableViewShare, enableViewShare, type View, type ViewShareCreated } from "@/lib/api/views";

interface Props {
  view: View;
  onChange: (view: View) => void;
}

/**
 * Turn anonymous access on for THIS view.
 *
 * Deliberately a per-view switch with the consequences spelled out next to it:
 * anyone with the link gets in, so the operator should see what they are handing
 * out at the moment they decide. The raw link is shown once — the server stores
 * only a hash — so the copy control is the only chance to keep it.
 */
export function ShareViewCard({ view, onChange }: Props) {
  const live = !!view.public_enabled_at;
  const [recordId, setRecordId] = useState(view.public_record_id ?? "");
  const [follow, setFollow] = useState(view.public_record_follow ?? false);
  const [created, setCreated] = useState<ViewShareCreated | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const entityBound = !!view.entity_definition_id;

  const enable = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await enableViewShare(view.id, {
        record_id: follow ? null : recordId.trim() || null,
        record_follow: follow,
      });
      setCreated(result);
      onChange({
        ...view,
        public_enabled_at: new Date().toISOString(),
        public_record_id: result.record_id,
        public_record_follow: result.record_follow,
        public_expires_at: result.expires_at,
      });
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Couldn't enable sharing"));
    } finally {
      setBusy(false);
    }
  };

  const disable = async () => {
    if (!window.confirm("Revoke the public link? Anyone using it right now will lose access.")) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await disableViewShare(view.id);
      setCreated(null);
      onChange(updated);
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Couldn't revoke the link"));
    } finally {
      setBusy(false);
    }
  };

  const copy = () => {
    if (!created) return;
    void navigator.clipboard?.writeText(created.url).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    });
  };

  return (
    <Card>
      <CardContent className="space-y-4 pt-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <Globe className="h-5 w-5 text-muted-foreground" />
            <h2 className="text-lg font-semibold">Anonymous access</h2>
            <span
              className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                live ? "bg-warning/15 text-warning" : "bg-muted text-muted-foreground"
              }`}
            >
              {live ? "On — anyone with the link" : "Off"}
            </span>
          </div>
          {live ? (
            <div className="flex gap-2">
              <Button type="button" variant="outline" size="sm" onClick={() => void enable()} disabled={busy}>
                <RotateCw className="h-4 w-4" /> New link
              </Button>
              <Button type="button" variant="destructive" size="sm" onClick={() => void disable()} disabled={busy}>
                <Link2Off className="h-4 w-4" /> Revoke
              </Button>
            </div>
          ) : null}
        </div>

        <p className="text-sm text-muted-foreground">
          Opens this one page to people with no KM2 login — a tablet on a shared desk, a screen on a
          wall. The link is the key: anyone who has it can see this page and use the buttons on it.
          Nothing else in the org is reachable through it, and the record it shows is chosen by the
          server — either the one you pick now, or the newest one each time if you tick the option
          below.
        </p>

        {!live ? (
          <div className="flex flex-wrap items-end gap-3">
            {entityBound ? (
              <div className="flex-1 space-y-2">
                <label className="block text-sm">
                  <span className="mb-1 block font-medium">Record to show</span>
                  <input
                    className="w-full rounded-md border bg-background px-3 py-2 font-mono text-sm disabled:opacity-50"
                    placeholder="record id (uuid)"
                    value={recordId}
                    disabled={follow}
                    onChange={(e) => setRecordId(e.target.value)}
                  />
                  <span className="mt-1 block text-xs text-muted-foreground">
                    Visitors always see this record — the link can&apos;t be edited to reach another one.
                  </span>
                </label>
                <label className="flex items-start gap-2 text-sm">
                  <input
                    type="checkbox"
                    className="mt-1"
                    checked={follow}
                    onChange={(e) => setFollow(e.target.checked)}
                  />
                  <span>
                    <span className="font-medium">Always show the newest record</span>
                    <span className="mt-0.5 block text-xs text-muted-foreground">
                      For a page about whatever is happening now, where each run creates a new
                      record — a class quiz, a live session board. Without this the link keeps
                      showing the record you picked today, which goes blank once the next one
                      starts. The server still chooses the record; visitors still can&apos;t pick.
                    </span>
                  </span>
                </label>
              </div>
            ) : null}
            <Button type="button" onClick={() => void enable()} disabled={busy}>
              {busy ? "Working…" : "Create public link"}
            </Button>
          </div>
        ) : null}

        {created ? (
          <div className="space-y-2 rounded-md border bg-muted/40 p-3">
            <p className="text-sm font-medium">
              Copy this now — it&apos;s shown once. We only store a fingerprint of it, so it can be
              replaced but never looked up again.
            </p>
            <button
              type="button"
              onClick={copy}
              className="flex w-full items-center gap-2 rounded-md border bg-background px-3 py-2 text-left font-mono text-xs hover:bg-muted"
            >
              {copied ? <Check className="h-4 w-4 shrink-0" /> : <Copy className="h-4 w-4 shrink-0" />}
              <span className="truncate">{created.url}</span>
            </button>
            {created.unsupported_elements.length ? (
              <p className="flex items-start gap-2 text-xs text-warning">
                <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" />
                <span>
                  This view uses {created.unsupported_elements.join(", ")}, which load their own data
                  from endpoints that need a login. Those parts will be empty for anonymous visitors.
                </span>
              </p>
            ) : null}
          </div>
        ) : null}

        {live && !created ? (
          <p className="text-xs text-muted-foreground">
            A link is active. It isn&apos;t shown again — use <strong>New link</strong> to replace it
            (the old one stops working immediately) or <strong>Revoke</strong> to turn sharing off.
          </p>
        ) : null}

        {error ? <p className="text-sm text-destructive">{error}</p> : null}
      </CardContent>
    </Card>
  );
}
