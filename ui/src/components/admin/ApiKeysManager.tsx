"use client";

import { ExternalLink, KeyRound, Plus, Trash2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { SecretField } from "@/components/ui/secret-field";
import { Skeleton } from "@/components/ui/skeleton";
import {
  createApiKey,
  listApiKeys,
  listScopes,
  revokeApiKey,
  type ApiKey,
  type ApiKeyCreated,
  type ApiKeyStatus,
  type NamedRef,
  type ScopeInfo,
} from "@/lib/api/apiKeys";
import { listAllDimensions, type DimensionKind } from "@/lib/api/dimensions";
import { getApiErrorMessage } from "@/lib/api/errors";
import { listAllFolders } from "@/lib/api/folders";
import { cn } from "@/lib/utils";

import { describeMintError, droppedScopes, effectiveScopes } from "./apiKeyForm";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";
const V1_BASE = `${API_BASE.replace(/\/$/, "")}/v1`;

// Expiry presets → days (null = never). Kept simple so admins don't fight a date
// picker; the ISO instant is computed at submit time.
const EXPIRY_PRESETS: ReadonlyArray<{ label: string; days: number | null }> = [
  { label: "Never", days: null },
  { label: "30 days", days: 30 },
  { label: "90 days", days: 90 },
  { label: "1 year", days: 365 },
];

const selectClass = "h-9 rounded-md border bg-background px-2 text-sm";

// Scopes whose routes honour a scoped key's assignments (mirrors SCOPED_KEY_SCOPES
// in services/api/src/api/services/api_key_scopes.py; the server enforces it).
const SCOPED_KEY_SCOPES: ReadonlySet<string> = new Set([
  "search:read",
  "knowledge:read",
  "knowledge:write",
  "agents:read",
  "agents:run",
  "work_orders:read",
  "work_orders:write",
]);

type AssignmentKind = DimensionKind | "folders";

// Order + labels for the assignment pickers and the key list.
const ASSIGNMENT_KINDS: ReadonlyArray<{ kind: AssignmentKind; label: string }> = [
  { kind: "regions", label: "Regions" },
  { kind: "roles", label: "Roles" },
  { kind: "groups", label: "Groups" },
  { kind: "departments", label: "Departments" },
  { kind: "folders", label: "Folders" },
];

type AssignmentOptions = Record<AssignmentKind, NamedRef[]>;
type AssignmentSelection = Record<AssignmentKind, Set<string>>;

const EMPTY_OPTIONS: AssignmentOptions = { regions: [], roles: [], groups: [], departments: [], folders: [] };

function emptySelection(): AssignmentSelection {
  return { regions: new Set(), roles: new Set(), groups: new Set(), departments: new Set(), folders: new Set() };
}

function hasAssignments(sel: AssignmentSelection): boolean {
  return ASSIGNMENT_KINDS.some(({ kind }) => sel[kind].size > 0);
}

/** "Roles: Analyst · Folders: Projects.Weekly" for a scoped key. */
function describeAssignments(key: ApiKey): string {
  return ASSIGNMENT_KINDS.filter(({ kind }) => key[kind].length > 0)
    .map(({ kind, label }) => `${label}: ${key[kind].map((r) => r.name).join(", ")}`)
    .join(" · ");
}

interface LoadedOptions {
  options: AssignmentOptions;
  /** Kinds with more values than the pickers loaded (see fetchAllPages). */
  truncated: AssignmentKind[];
}

async function loadAssignmentOptions(): Promise<LoadedOptions> {
  const [regions, roles, groups, departments, folders] = await Promise.all([
    listAllDimensions("regions"),
    listAllDimensions("roles"),
    listAllDimensions("groups"),
    listAllDimensions("departments"),
    listAllFolders(),
  ]);
  const refs = (items: { id: string; name: string }[]) => items.map(({ id, name }) => ({ id, name }));
  const pages = { regions, roles, groups, departments, folders };
  return {
    options: {
      regions: refs(regions.items),
      roles: refs(roles.items),
      groups: refs(groups.items),
      departments: refs(departments.items),
      folders: folders.items
        .map((f) => ({ id: f.id, name: f.dot_path || f.name }))
        .sort((a, b) => a.name.localeCompare(b.name)),
    },
    truncated: ASSIGNMENT_KINDS.map(({ kind }) => kind).filter((kind) => pages[kind].truncated),
  };
}

/** id → what the pickers show for it (folder path or dimension name). */
function optionNames(options: AssignmentOptions): Map<string, string> {
  const names = new Map<string, string>();
  for (const { kind } of ASSIGNMENT_KINDS) {
    for (const opt of options[kind]) names.set(opt.id.toLowerCase(), opt.name);
  }
  return names;
}

const STATUS_STYLES: Record<ApiKeyStatus, string> = {
  active: "border-emerald-600/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
  revoked: "border-destructive/30 bg-destructive/10 text-destructive",
  expired: "border-amber-600/30 bg-amber-500/10 text-amber-700 dark:text-amber-500",
};

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleDateString();
}

export function ApiKeysManager() {
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [scopes, setScopes] = useState<ScopeInfo[]>([]);
  const [options, setOptions] = useState<AssignmentOptions>(EMPTY_OPTIONS);
  const [truncatedKinds, setTruncatedKinds] = useState<AssignmentKind[]>([]);
  const [optionsError, setOptionsError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [selectedScopes, setSelectedScopes] = useState<Set<string>>(new Set());
  const [expiryDays, setExpiryDays] = useState<number | null>(null);
  // Empty = organization-wide; any selection makes the key scoped.
  const [assigned, setAssigned] = useState<AssignmentSelection>(emptySelection);
  const scoped = hasAssignments(assigned);
  // Derived, never written back: toggling assignments must not lose a selection.
  const sendScopes = effectiveScopes(selectedScopes, scoped, SCOPED_KEY_SCOPES);
  const leftOut = droppedScopes(selectedScopes, scoped, SCOPED_KEY_SCOPES);
  const [creating, setCreating] = useState(false);
  // The plaintext key is returned once; hold it to show the copy dialog.
  const [created, setCreated] = useState<ApiKeyCreated | null>(null);

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const [keyList, scopeList] = await Promise.all([listApiKeys(), listScopes()]);
      setKeys(keyList);
      setScopes(scopeList);
      // The assignment pickers are optional: if they fail, the keys still load
      // and organization-wide keys can still be created.
      try {
        const loaded = await loadAssignmentOptions();
        setOptions(loaded.options);
        setTruncatedKinds(loaded.truncated);
        setOptionsError(null);
      } catch (e: unknown) {
        setOptions(EMPTY_OPTIONS);
        setTruncatedKinds([]);
        setOptionsError(
          getApiErrorMessage(e, "Couldn't load regions, roles, groups, departments or folders; only organization-wide keys can be created right now"),
        );
      }
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to load API keys"));
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const canSubmit = name.trim().length > 0 && sendScopes.size > 0 && !creating;

  const toggleAssignment = (kind: AssignmentKind, id: string) => {
    // Pure updater (StrictMode may run it twice); scopes are derived, not pruned here.
    setAssigned((prev) => {
      const next = { ...prev, [kind]: new Set(prev[kind]) };
      if (next[kind].has(id)) next[kind].delete(id);
      else next[kind].add(id);
      return next;
    });
  };

  const toggleScope = (scope: string) => {
    setSelectedScopes((prev) => {
      const next = new Set(prev);
      if (next.has(scope)) next.delete(scope);
      else next.add(scope);
      return next;
    });
  };

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    setCreating(true);
    setError(null);
    try {
      const expires_at =
        expiryDays === null ? null : new Date(Date.now() + expiryDays * 86_400_000).toISOString();
      const result = await createApiKey({
        name: name.trim(),
        scopes: [...sendScopes],
        expires_at,
        region_ids: [...assigned.regions],
        role_ids: [...assigned.roles],
        group_ids: [...assigned.groups],
        department_ids: [...assigned.departments],
        folder_ids: [...assigned.folders],
      });
      setCreated(result); // show the one-time key dialog
      setName("");
      setSelectedScopes(new Set());
      setExpiryDays(null);
      setAssigned(emptySelection());
      await load();
    } catch (e: unknown) {
      // Name the folders/dimensions the server refers to by id.
      setError(describeMintError(getApiErrorMessage(e, "Failed to create API key"), optionNames(options)));
    } finally {
      setCreating(false);
    }
  };

  const handleRevoke = async (key: ApiKey) => {
    if (!confirm(`Revoke API key "${key.name}"? Any integration using it will stop working immediately.`))
      return;
    setError(null);
    try {
      await revokeApiKey(key.id);
      await load();
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to revoke API key"));
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold">API keys</h2>
        <p className="text-sm text-muted-foreground">
          Grant external systems programmatic access to this organization&rsquo;s data over the REST
          API, limited to the scopes you select. A key either sees the whole organization&rsquo;s
          knowledge, or is limited to the regions, roles, groups and departments you assign it
          &mdash; exactly what a member holding them would see &mdash; optionally narrowed further
          to chosen folders.
        </p>
      </div>

      {error ? <p className="text-sm text-destructive">{error}</p> : null}

      <Card>
        <CardContent className="space-y-4 pt-6">
          <form onSubmit={handleCreate} className="space-y-4">
            <div className="flex flex-wrap items-end gap-2">
              <div className="min-w-48 flex-1">
                <label className="mb-1 block text-sm font-medium">Key name</label>
                <Input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="e.g. Data warehouse sync"
                />
              </div>
              <div>
                <label className="mb-1 block text-sm font-medium">Expires</label>
                <select
                  className={selectClass}
                  value={expiryDays === null ? "never" : String(expiryDays)}
                  onChange={(e) => setExpiryDays(e.target.value === "never" ? null : Number(e.target.value))}
                >
                  {EXPIRY_PRESETS.map((p) => (
                    <option key={p.label} value={p.days === null ? "never" : String(p.days)}>
                      {p.label}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <fieldset className="space-y-3 rounded-md border p-3">
              <legend className="px-1 text-sm font-medium">Knowledge access</legend>
              <p className="text-xs text-muted-foreground">
                Leave everything unselected for an organization-wide key. Pick regions, roles, groups
                or departments to make the key read exactly what a member holding them would. Pick
                folders to narrow it further to those folders and their subfolders &mdash; folders
                never grant access the assignments don&rsquo;t have, and a key with only folders reads
                like a member with no assignments. Such a key can only hold search, knowledge, agent
                and work-order scopes, and its assignments can&rsquo;t be changed later (revoke and
                re-issue it instead). Moving a folder under a chosen folder widens the key&rsquo;s
                folders to include it.
              </p>
              {optionsError ? <p className="text-xs text-amber-700 dark:text-amber-500">{optionsError}</p> : null}
              {truncatedKinds.length > 0 ? (
                <p className="text-xs text-amber-700 dark:text-amber-500">
                  Only the first{" "}
                  {truncatedKinds
                    .map((kind) => ASSIGNMENT_KINDS.find((k) => k.kind === kind)?.label.toLowerCase() ?? kind)
                    .join(", ")}{" "}
                  are listed here; this organization has more than the pickers can show.
                </p>
              ) : null}
              {ASSIGNMENT_KINDS.map(({ kind, label }) => (
                <div key={kind}>
                  <p className="mb-1.5 text-sm font-medium">{label}</p>
                  {options[kind].length === 0 ? (
                    <p className="text-xs text-muted-foreground">None defined.</p>
                  ) : (
                    <div className="flex flex-wrap gap-2" role="group" aria-label={label}>
                      {options[kind].map((opt) => {
                        const active = assigned[kind].has(opt.id);
                        return (
                          <button
                            key={opt.id}
                            type="button"
                            aria-pressed={active}
                            onClick={() => toggleAssignment(kind, opt.id)}
                            className={cn(
                              "rounded-md border px-2 py-1 text-xs transition-colors",
                              active ? "border-primary bg-primary text-primary-foreground" : "hover:bg-accent",
                            )}
                          >
                            {opt.name}
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>
              ))}
            </fieldset>

            <div>
              <label className="mb-1 block text-sm font-medium">Scopes</label>
              {isLoading && scopes.length === 0 ? (
                <Skeleton className="h-24 w-full" />
              ) : (
                <div className="grid gap-1 sm:grid-cols-2">
                  {scopes.map((s) => (
                    <label
                      key={s.name}
                      className="flex cursor-pointer items-start gap-2 rounded-md border p-2 text-sm hover:bg-muted/40"
                    >
                      <input
                        type="checkbox"
                        className="mt-0.5"
                        checked={sendScopes.has(s.name)}
                        disabled={scoped && !SCOPED_KEY_SCOPES.has(s.name)}
                        onChange={() => toggleScope(s.name)}
                      />
                      <span className="min-w-0">
                        <code className="text-xs font-medium">{s.name}</code>
                        <span className="block text-xs text-muted-foreground">{s.description}</span>
                      </span>
                    </label>
                  ))}
                </div>
              )}
              {leftOut.length > 0 ? (
                <p className="mt-2 text-xs text-amber-700 dark:text-amber-500">
                  A key with assignments can&rsquo;t hold {leftOut.join(", ")}; {leftOut.length === 1 ? "it" : "they"}{" "}
                  will be left out unless you clear the assignments.
                </p>
              ) : null}
            </div>

            <Button type="submit" disabled={!canSubmit}>
              <Plus className="h-4 w-4" />
              Create key
            </Button>
          </form>
        </CardContent>
      </Card>

      {isLoading ? (
        <Skeleton className="h-24 w-full" />
      ) : keys.length === 0 ? (
        <p className="text-sm text-muted-foreground">No API keys yet.</p>
      ) : (
        <ul className="divide-y rounded-md border">
          {keys.map((key) => (
            <li key={key.id} className="flex items-center gap-3 px-4 py-3">
              <KeyRound className="h-4 w-4 shrink-0 text-muted-foreground" />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate text-sm font-medium">{key.name}</span>
                  <span
                    className={`rounded-full border px-2 py-0.5 text-xs ${STATUS_STYLES[key.status]}`}
                  >
                    {key.status}
                  </span>
                </div>
                <div className="truncate text-xs text-muted-foreground">
                  <code>{key.key_prefix}…</code> ·{" "}
                  {key.access_mode === "scoped" ? "limited" : "organization-wide"} ·{" "}
                  {key.scopes.length} scope
                  {key.scopes.length === 1 ? "" : "s"} · last used {fmtDate(key.last_used_at)} · expires{" "}
                  {key.expires_at ? fmtDate(key.expires_at) : "never"}
                </div>
                {key.access_mode === "scoped" ? (
                  <div className="truncate text-xs text-muted-foreground" title={describeAssignments(key)}>
                    {describeAssignments(key) || key.assignments_note || "No assignments left — this key is refused"}
                  </div>
                ) : null}
              </div>
              {key.status !== "revoked" ? (
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => void handleRevoke(key)}
                  aria-label={`Revoke ${key.name}`}
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              ) : null}
            </li>
          ))}
        </ul>
      )}

      <DeveloperPanel scopes={scopes} />

      {created ? <KeyDialog created={created} onClose={() => setCreated(null)} /> : null}
    </div>
  );
}

function DeveloperPanel({ scopes }: { scopes: ScopeInfo[] }) {
  const example = useMemo(
    () => `curl -H "Authorization: Bearer km2_..." \\\n  ${V1_BASE}/entities`,
    [],
  );
  return (
    <Card>
      <CardContent className="space-y-3 pt-6 text-sm">
        <div className="flex items-center justify-between gap-2">
          <h3 className="font-semibold">Using the API</h3>
          <a
            href={`${V1_BASE}/docs`}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
          >
            API reference <ExternalLink className="h-3 w-3" />
          </a>
        </div>
        <p className="text-muted-foreground">
          Base URL: <code className="rounded bg-muted px-1">{V1_BASE}</code>. Authenticate with{" "}
          <code className="rounded bg-muted px-1">Authorization: Bearer km2_…</code> (or{" "}
          <code className="rounded bg-muted px-1">X-API-Key</code>).
        </p>
        <pre className="overflow-x-auto rounded-md border bg-muted/40 p-3 text-xs">{example}</pre>
        <details className="text-xs text-muted-foreground">
          <summary className="cursor-pointer font-medium text-foreground">Scope reference</summary>
          <ul className="mt-2 space-y-1">
            {scopes.map((s) => (
              <li key={s.name}>
                <code className="rounded bg-muted px-1">{s.name}</code> — {s.description}
              </li>
            ))}
          </ul>
        </details>
      </CardContent>
    </Card>
  );
}

function KeyDialog({ created, onClose }: { created: ApiKeyCreated; onClose: () => void }) {
  return (
    <Dialog open onClose={onClose}>
      <DialogHeader>
        <DialogTitle>API key created</DialogTitle>
        <DialogDescription>
          Copy the key now — it is <strong>shown only once</strong> and cannot be recovered. Store it
          somewhere safe; if you lose it, revoke this key and create a new one.
        </DialogDescription>
      </DialogHeader>

      <div className="space-y-3">
        <SecretField label={`Key for “${created.name}”`} value={created.key} />
        <div className="rounded-md border border-dashed p-3 text-xs text-muted-foreground">
          <p className="mb-1 font-medium text-foreground">Grants</p>
          <p>
            {created.access_mode === "scoped"
              ? `This key reads only what its assignments allow (${describeAssignments(created)}), limited to: `
              : "This key can act with organization-wide access, limited to: "}
            {created.scopes.map((s) => (
              <code key={s} className="mr-1 rounded bg-muted px-1">
                {s}
              </code>
            ))}
          </p>
        </div>
      </div>

      <DialogFooter>
        <Button onClick={onClose}>Done</Button>
      </DialogFooter>
    </Dialog>
  );
}
