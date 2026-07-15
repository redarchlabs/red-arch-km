"use client";

import { AlertTriangle, ArrowLeft, KeyRound, Rocket, Undo2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getApiErrorMessage } from "@/lib/api/errors";
import type { BundleDiff, CollisionStrategy, GeneratedSecret } from "@/lib/api/migration";
import {
  approveRelease,
  diffRelease,
  getRelease,
  promoteRelease,
  rejectRelease,
  rollbackPromotion,
  submitRelease,
  type InFlightBlocker,
  type PromotionTarget,
  type ReleaseDetail,
} from "@/lib/api/promotions";
import { isAxiosError } from "axios";

import { DiffTable, StatusBadge } from "./parts";

export function ReleaseDetailView({
  releaseId,
  targets,
  onBack,
  onChanged,
}: {
  releaseId: string;
  targets: PromotionTarget[];
  onBack: () => void;
  onChanged: () => void;
}) {
  const [detail, setDetail] = useState<ReleaseDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [targetId, setTargetId] = useState<string>("");
  const [diff, setDiff] = useState<BundleDiff | null>(null);
  const [blockers, setBlockers] = useState<InFlightBlocker[]>([]);
  const [override, setOverride] = useState(false);
  const [secrets, setSecrets] = useState<GeneratedSecret[]>([]);
  // Promoting a release means "make the target match it", so default to overwrite
  // (skip would leave every already-present object untouched — a silent no-op).
  const [strategy, setStrategy] = useState<CollisionStrategy>("overwrite");
  const [confirmRollbackId, setConfirmRollbackId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setDetail(await getRelease(releaseId));
  }, [releaseId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!detail) {
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  }
  const { release, items, approvals, promotions } = detail;
  const enabledTargets = targets.filter((t) => t.enabled);

  async function act(fn: () => Promise<unknown>, done: string) {
    setBusy(true);
    try {
      await fn();
      await load();
      onChanged();
      toast.success(done);
    } catch (error) {
      toast.error("Action failed", { description: getApiErrorMessage(error, "Could not complete the action.") });
    } finally {
      setBusy(false);
    }
  }

  async function runDiff() {
    if (!targetId) return toast.error("Choose a target first.");
    setBusy(true);
    setDiff(null);
    setBlockers([]);
    try {
      const result = await diffRelease(releaseId, { target_id: targetId, strategy, apply_deletes: true });
      setDiff(result);
    } catch (error) {
      toast.error("Diff failed", { description: getApiErrorMessage(error, "Could not diff this release.") });
    } finally {
      setBusy(false);
    }
  }

  async function runPromote() {
    if (!targetId) return toast.error("Choose a target first.");
    setBusy(true);
    setSecrets([]);
    try {
      const res = await promoteRelease(releaseId, { target_id: targetId, strategy, override_inflight: override });
      setSecrets(res.generated_secrets);
      setBlockers([]);
      await load();
      onChanged();
      toast.success("Promoted", { description: "Review the result below." });
    } catch (error) {
      if (isAxiosError(error) && error.response?.status === 409) {
        const detailBody = error.response.data?.detail;
        setBlockers(detailBody?.blockers ?? []);
        toast.warning("Blocked by in-flight runs", { description: "Wait for them to finish, or override." });
      } else {
        toast.error("Promote failed", { description: getApiErrorMessage(error, "Could not promote.") });
      }
    } finally {
      setBusy(false);
    }
  }

  const target = enabledTargets.find((t) => t.id === targetId);

  return (
    <div className="space-y-6">
      <Button variant="ghost" size="sm" onClick={onBack} className="-ml-2">
        <ArrowLeft className="mr-1 h-4 w-4" /> All releases
      </Button>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            {release.name}
            <StatusBadge status={release.status} />
          </CardTitle>
          <CardDescription>{release.description || "No description."}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Governance actions */}
          <div className="flex flex-wrap gap-2">
            {release.status === "draft" || release.status === "rejected" ? (
              <Button size="sm" disabled={busy} onClick={() => void act(() => submitRelease(releaseId), "Submitted for review")}>
                Submit for review
              </Button>
            ) : null}
            {release.status === "in_review" ? (
              <>
                <Button size="sm" disabled={busy} onClick={() => void act(() => approveRelease(releaseId), "Approved")}>
                  Approve
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busy}
                  onClick={() => void act(() => rejectRelease(releaseId), "Rejected")}
                >
                  Request changes
                </Button>
              </>
            ) : null}
          </div>

          {/* Frozen contents */}
          <div>
            <p className="mb-1 text-sm font-medium">Frozen contents ({items.length} objects)</p>
            <div className="flex flex-wrap gap-1.5">
              {summarizeItems(items).map(([type, count]) => (
                <span key={type} className="rounded border bg-muted/40 px-2 py-0.5 text-xs">
                  {type.replace(/_/g, " ")}: {count}
                </span>
              ))}
            </div>
          </div>

          {approvals.length > 0 ? (
            <div className="text-xs text-muted-foreground">
              {approvals.map((a) => (
                <div key={a.id}>
                  {a.decision === "approved" ? "✓ approved" : "✗ changes requested"}
                  {a.comment ? ` — ${a.comment}` : ""}
                </div>
              ))}
            </div>
          ) : null}
        </CardContent>
      </Card>

      {/* Promote panel — only for an approved release */}
      {release.status === "approved" ? (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Rocket className="h-4 w-4" /> Promote
            </CardTitle>
            <CardDescription>Preview the impact on a target, then promote.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-wrap items-center gap-2">
              <select
                value={targetId}
                onChange={(e) => {
                  setTargetId(e.target.value);
                  setDiff(null);
                  setBlockers([]);
                }}
                className="rounded-md border bg-background px-3 py-2 text-sm"
              >
                <option value="">Choose a target…</option>
                {enabledTargets.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name} ({t.kind === "local_org" ? "local org" : "remote"})
                  </option>
                ))}
              </select>
              <select
                value={strategy}
                onChange={(e) => setStrategy(e.target.value as CollisionStrategy)}
                className="rounded-md border bg-background px-3 py-2 text-sm"
                title="How to handle objects that already exist on the target"
              >
                <option value="overwrite">Overwrite existing</option>
                <option value="skip">Skip existing</option>
                <option value="rename">Rename on collision</option>
              </select>
              <Button variant="outline" size="sm" disabled={busy || !targetId} onClick={() => void runDiff()}>
                Preview diff
              </Button>
              <Button size="sm" disabled={busy || !targetId} onClick={() => void runPromote()}>
                {busy ? "Working…" : "Promote"}
              </Button>
            </div>
            {target ? (
              <p className="text-xs text-muted-foreground">
                Promoting <span className="font-medium">{release.name}</span> →{" "}
                <span className="font-medium">{target.name}</span>{" "}
                {target.kind === "local_org" ? "(another org, same database)" : `(remote: ${target.base_url})`}
              </p>
            ) : null}

            {diff ? (
              <div className="space-y-2">
                {diff.has_deletes ? (
                  <div className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 p-2 text-xs">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
                    <span>
                      {diff.totals.deleted} object(s) exist on the target but not in this release. Deletion is
                      gated and not applied by a plain promote.
                    </span>
                  </div>
                ) : null}
                <DiffTable diff={diff} />
              </div>
            ) : null}

            {blockers.length > 0 ? (
              <div className="space-y-2 rounded-md border border-amber-500/40 bg-amber-500/5 p-3 text-xs">
                <p className="font-medium text-amber-600 dark:text-amber-400">
                  Blocked by in-flight workflow runs
                </p>
                <ul className="list-disc space-y-0.5 pl-5">
                  {blockers.map((b) => (
                    <li key={b.lineage_id}>
                      {b.resource_type}: {b.name} — {b.reason}
                    </li>
                  ))}
                </ul>
                <label className="flex items-center gap-2">
                  <input type="checkbox" checked={override} onChange={(e) => setOverride(e.target.checked)} />
                  Override and promote anyway (may disrupt those runs)
                </label>
              </div>
            ) : null}

            {secrets.length > 0 ? (
              <div className="space-y-2 rounded-md border border-amber-500/40 bg-amber-500/5 p-3">
                <p className="flex items-center gap-2 text-sm font-medium">
                  <KeyRound className="h-4 w-4" /> New webhook credentials on the target — copy now
                </p>
                <ul className="space-y-1 font-mono text-xs">
                  {secrets.map((s, i) => (
                    <li key={i} className="break-all">
                      {s.name}: {s.url}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </CardContent>
        </Card>
      ) : null}

      {/* History */}
      <Card>
        <CardHeader>
          <CardTitle>Promotion history</CardTitle>
        </CardHeader>
        <CardContent>
          {promotions.length === 0 ? (
            <p className="text-sm text-muted-foreground">Not promoted yet.</p>
          ) : (
            <div className="space-y-2">
              {promotions.map((p) => (
                <div key={p.id} className="border-b pb-2 text-sm last:border-0">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <StatusBadge status={p.status} />
                      <span className="font-medium">{p.target_label}</span>
                      <span className="text-xs text-muted-foreground">
                        {p.rollback_source_id ? "(rollback)" : p.strategy}
                      </span>
                    </div>
                    {p.status === "promoted" && !p.rollback_source_id ? (
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={busy}
                        onClick={() => setConfirmRollbackId((cur) => (cur === p.id ? null : p.id))}
                      >
                        <Undo2 className="mr-1 h-3.5 w-3.5" /> Roll back
                      </Button>
                    ) : null}
                  </div>
                  {confirmRollbackId === p.id ? (
                    <div className="mt-2 flex flex-col gap-2 rounded-md border border-destructive/40 bg-destructive/5 p-2 text-xs">
                      <div className="flex items-start gap-2">
                        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
                        <span>
                          Rolling back restores the target to its state before this promotion:{" "}
                          <strong>any config created on the target since then is deleted</strong>, and
                          in-flight workflow runs may be disrupted. This cannot be undone.
                        </span>
                      </div>
                      <div className="flex gap-2">
                        <Button
                          size="sm"
                          variant="destructive"
                          disabled={busy}
                          onClick={() =>
                            void act(async () => {
                              await rollbackPromotion(p.id);
                              setConfirmRollbackId(null);
                            }, "Rolled back")
                          }
                        >
                          Yes, roll back
                        </Button>
                        <Button size="sm" variant="ghost" disabled={busy} onClick={() => setConfirmRollbackId(null)}>
                          Cancel
                        </Button>
                      </div>
                    </div>
                  ) : null}
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function summarizeItems(items: { object_type: string }[]): [string, number][] {
  const counts = new Map<string, number>();
  for (const i of items) counts.set(i.object_type, (counts.get(i.object_type) ?? 0) + 1);
  return [...counts.entries()].sort((a, b) => a[0].localeCompare(b[0]));
}
