"use client";

import { AlertTriangle, KeyRound, Upload } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  importOrg,
  type CollisionStrategy,
  type ImportSummary,
  type ResourceOutcome,
  type Selection,
} from "@/lib/api/migration";
import { getApiErrorMessage } from "@/lib/api/errors";
import { cn } from "@/lib/utils";

import { ResourceSelector } from "./ResourceSelector";
import { bundleToGroups, countSelected, selectAll, type SelectableGroup } from "./selection";

const STRATEGIES: ReadonlyArray<{ key: CollisionStrategy; label: string; hint: string }> = [
  { key: "skip", label: "Skip existing", hint: "Keep what's already here; map references onto it." },
  { key: "rename", label: "Rename incoming", hint: "Create a suffixed copy alongside the existing one." },
  { key: "overwrite", label: "Overwrite", hint: "Update the existing resource in place where possible." },
];

const OUTCOME_KEYS: ReadonlyArray<keyof ResourceOutcome> = [
  "created",
  "overwritten",
  "renamed",
  "skipped",
  "failed",
];

const BUNDLE_KIND = "km2-migration-bundle";

export function ImportPanel() {
  const [file, setFile] = useState<File | null>(null);
  const [groups, setGroups] = useState<SelectableGroup[] | null>(null);
  const [selection, setSelection] = useState<Selection>({});
  const [parseError, setParseError] = useState<string | null>(null);
  const [strategy, setStrategy] = useState<CollisionStrategy>("skip");
  const [busy, setBusy] = useState(false);
  const [summary, setSummary] = useState<ImportSummary | null>(null);

  async function onFileChange(chosen: File | null) {
    setFile(chosen);
    setGroups(null);
    setSelection({});
    setSummary(null);
    setParseError(null);
    if (!chosen) return;
    try {
      const bundle = JSON.parse(await chosen.text());
      if (bundle?.kind !== BUNDLE_KIND || !bundle?.resources) {
        setParseError("This file is not a KM2 migration bundle.");
        return;
      }
      const next = bundleToGroups(bundle.resources);
      setGroups(next);
      setSelection(selectAll(next));
    } catch {
      setParseError("Could not read this file as JSON.");
    }
  }

  async function runImport(dryRun: boolean) {
    if (!file) {
      toast.error("Choose a bundle file first.");
      return;
    }
    setBusy(true);
    setSummary(null);
    try {
      const result = await importOrg({ file, strategy, dryRun, selection });
      setSummary(result);
      toast.success(dryRun ? "Preview complete" : "Import complete", {
        description: dryRun
          ? "No changes were saved — review the summary below."
          : "Review the summary below.",
      });
    } catch (error) {
      toast.error("Import failed", { description: getApiErrorMessage(error, "Could not import this bundle.") });
    } finally {
      setBusy(false);
    }
  }

  const selected = countSelected(selection);

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Import a bundle</CardTitle>
          <CardDescription>
            Upload an exported bundle, choose which objects to bring into <span className="font-medium">this</span>{" "}
            organization, then run a preview to see what would change before saving.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="space-y-2">
            <label className="text-sm font-medium" htmlFor="bundle-file">
              Bundle file
            </label>
            <input
              id="bundle-file"
              type="file"
              accept="application/json,.json"
              onChange={(e) => void onFileChange(e.target.files?.[0] ?? null)}
              className="block w-full text-sm text-muted-foreground file:mr-4 file:rounded-md file:border-0 file:bg-primary file:px-4 file:py-2 file:text-sm file:font-medium file:text-primary-foreground hover:file:bg-primary/90"
            />
            {parseError ? <p className="text-sm text-destructive">{parseError}</p> : null}
          </div>

          {groups && groups.length > 0 ? (
            <>
              <div className="space-y-2">
                <p className="text-sm font-medium">Choose what to import</p>
                <ResourceSelector groups={groups} selection={selection} onChange={setSelection} />
                <p className="text-xs text-muted-foreground">
                  Tip: include an object&apos;s dependencies too (e.g. a form&apos;s entity), or its references
                  will be reported as unresolved.
                </p>
              </div>

              <fieldset className="space-y-2">
                <legend className="text-sm font-medium">On a name/slug collision</legend>
                <div className="grid gap-2 sm:grid-cols-3">
                  {STRATEGIES.map((s) => (
                    <button
                      key={s.key}
                      type="button"
                      onClick={() => setStrategy(s.key)}
                      aria-pressed={strategy === s.key}
                      className={cn(
                        "rounded-md border p-3 text-left text-sm transition-colors",
                        strategy === s.key ? "border-primary bg-accent" : "border-border hover:bg-accent/50",
                      )}
                    >
                      <span className="block font-medium">{s.label}</span>
                      <span className="mt-1 block text-xs text-muted-foreground">{s.hint}</span>
                    </button>
                  ))}
                </div>
              </fieldset>

              <div className="flex flex-wrap items-center gap-2">
                <Button variant="outline" onClick={() => runImport(true)} disabled={busy || selected === 0}>
                  {busy ? "Working…" : "Preview (dry run)"}
                </Button>
                <Button onClick={() => runImport(false)} disabled={busy || selected === 0}>
                  <Upload className="mr-2 h-4 w-4" />
                  {busy ? "Importing…" : `Import ${selected} object${selected === 1 ? "" : "s"}`}
                </Button>
              </div>
            </>
          ) : null}
        </CardContent>
      </Card>

      {summary ? <ImportSummaryView summary={summary} /> : null}
    </div>
  );
}

function ImportSummaryView({ summary }: { summary: ImportSummary }) {
  const rows = Object.entries(summary.resources).filter(([, o]) =>
    OUTCOME_KEYS.some((k) => o[k] > 0),
  );
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          {summary.dry_run ? "Preview summary" : "Import summary"}
          {summary.dry_run ? (
            <span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-xs font-medium text-amber-600 dark:text-amber-400">
              nothing saved
            </span>
          ) : null}
        </CardTitle>
        <CardDescription>Strategy: {summary.strategy}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        {rows.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-muted-foreground">
                  <th className="py-1 pr-4 font-medium">Resource</th>
                  {OUTCOME_KEYS.map((k) => (
                    <th key={k} className="px-2 py-1 text-right font-medium capitalize">
                      {k}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map(([kind, o]) => (
                  <tr key={kind} className="border-b last:border-0">
                    <td className="py-1 pr-4 font-medium capitalize">{kind.replace(/_/g, " ")}</td>
                    {OUTCOME_KEYS.map((k) => (
                      <td
                        key={k}
                        className={cn(
                          "px-2 py-1 text-right tabular-nums",
                          k === "failed" && o[k] > 0 ? "text-destructive" : "",
                          o[k] === 0 ? "text-muted-foreground/50" : "",
                        )}
                      >
                        {o[k]}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">Nothing to import.</p>
        )}

        {summary.generated_secrets.length > 0 ? (
          <div className="space-y-2 rounded-md border border-amber-500/40 bg-amber-500/5 p-3">
            <p className="flex items-center gap-2 text-sm font-medium">
              <KeyRound className="h-4 w-4" /> New webhook credentials — copy these now, they won&apos;t be shown
              again
            </p>
            <ul className="space-y-2 text-xs">
              {summary.generated_secrets.map((s, i) => (
                <li key={i} className="space-y-0.5 font-mono">
                  <div className="font-sans font-medium">{s.name}</div>
                  <div className="break-all">URL: {s.url}</div>
                  <div className="break-all">
                    {s.signature_header}: {s.signing_secret}
                  </div>
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {summary.errors.length > 0 ? (
          <div className="space-y-1">
            <p className="flex items-center gap-2 text-sm font-medium text-destructive">
              <AlertTriangle className="h-4 w-4" /> Errors ({summary.errors.length})
            </p>
            <ul className="list-disc space-y-0.5 pl-5 text-xs text-destructive">
              {summary.errors.map((e, i) => (
                <li key={i}>{e}</li>
              ))}
            </ul>
          </div>
        ) : null}

        {summary.warnings.length > 0 ? (
          <details className="text-xs">
            <summary className="cursor-pointer text-sm font-medium text-muted-foreground">
              Warnings ({summary.warnings.length})
            </summary>
            <ul className="mt-2 list-disc space-y-0.5 pl-5 text-muted-foreground">
              {summary.warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          </details>
        ) : null}
      </CardContent>
    </Card>
  );
}
