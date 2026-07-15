"use client";

import { GitBranch, Plus, Rocket, Server, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { ResourceSelector } from "@/components/import-export/ResourceSelector";
import {
  countSelected,
  manifestToGroups,
  selectAll,
  type SelectableGroup,
} from "@/components/import-export/selection";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useOrg } from "@/context/OrgContext";
import { getApiErrorMessage } from "@/lib/api/errors";
import { fetchManifest, type Selection } from "@/lib/api/migration";
import {
  createRelease,
  createTarget,
  deleteTarget,
  listReleases,
  listTargets,
  testTarget,
  type PromotionTarget,
  type Release,
  type TargetKind,
} from "@/lib/api/promotions";
import { cn } from "@/lib/utils";

import { ReleaseDetailView } from "./ReleaseDetailView";
import { StatusBadge } from "./parts";

type Tab = "releases" | "targets";

export function ChangeManagementConsole() {
  const { isOrgAdmin, orgs, currentOrgId } = useOrg();
  const [tab, setTab] = useState<Tab>("releases");
  const [releases, setReleases] = useState<Release[]>([]);
  const [targets, setTargets] = useState<PromotionTarget[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [r, t] = await Promise.all([listReleases(), listTargets()]);
      setReleases(r);
      setTargets(t);
    } catch (error) {
      toast.error("Could not load change management", {
        description: getApiErrorMessage(error, "Failed to load releases and targets."),
      });
    }
  }, []);

  useEffect(() => {
    if (isOrgAdmin) void refresh();
  }, [isOrgAdmin, refresh]);

  if (!isOrgAdmin) {
    return <p className="text-sm text-muted-foreground">Change management is available to organization admins.</p>;
  }

  if (selectedId) {
    return (
      <ReleaseDetailView
        releaseId={selectedId}
        targets={targets}
        onBack={() => setSelectedId(null)}
        onChanged={refresh}
      />
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex gap-1 border-b" role="tablist">
        {(["releases", "targets"] as const).map((t) => (
          <button
            key={t}
            role="tab"
            aria-selected={tab === t}
            onClick={() => setTab(t)}
            className={cn(
              "border-b-2 px-3 py-2 text-sm font-medium capitalize transition-colors",
              tab === t ? "border-primary text-foreground" : "border-transparent text-muted-foreground",
            )}
          >
            {t}
          </button>
        ))}
      </div>

      {tab === "releases" ? (
        <ReleasesTab releases={releases} onOpen={setSelectedId} onChanged={refresh} />
      ) : (
        <TargetsTab
          targets={targets}
          orgs={orgs.filter((o) => o.id !== currentOrgId && o.is_admin)}
          onChanged={refresh}
        />
      )}
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Releases
// --------------------------------------------------------------------------- //
function ReleasesTab({
  releases,
  onOpen,
  onChanged,
}: {
  releases: Release[];
  onOpen: (id: string) => void;
  onChanged: () => Promise<void>;
}) {
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [groups, setGroups] = useState<SelectableGroup[] | null>(null);
  const [selection, setSelection] = useState<Selection>({});
  const [busy, setBusy] = useState(false);

  async function openForm() {
    setCreating(true);
    try {
      const manifest = await fetchManifest();
      const next = manifestToGroups(manifest);
      setGroups(next);
      setSelection(selectAll(next));
    } catch (error) {
      toast.error("Could not load objects", { description: getApiErrorMessage(error, "") });
    }
  }

  async function submit() {
    if (!name.trim()) return toast.error("Name the release.");
    setBusy(true);
    try {
      await createRelease({ name: name.trim(), description: description.trim() || null, selection });
      toast.success("Release created");
      setCreating(false);
      setName("");
      setDescription("");
      setGroups(null);
      await onChanged();
    } catch (error) {
      toast.error("Could not create release", { description: getApiErrorMessage(error, "") });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          A release is a frozen snapshot of selected config you can review, approve, and promote.
        </p>
        {!creating ? (
          <Button size="sm" onClick={() => void openForm()}>
            <Plus className="mr-1 h-4 w-4" /> New release
          </Button>
        ) : null}
      </div>

      {creating ? (
        <Card>
          <CardHeader>
            <CardTitle>New release</CardTitle>
            <CardDescription>Name it, then choose which objects to freeze into the snapshot.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <Input placeholder="Release name (e.g. 2026-07 config)" value={name} onChange={(e) => setName(e.target.value)} />
            <Input placeholder="Description (optional)" value={description} onChange={(e) => setDescription(e.target.value)} />
            {groups ? (
              <ResourceSelector groups={groups} selection={selection} onChange={setSelection} />
            ) : (
              <p className="text-sm text-muted-foreground">Loading objects…</p>
            )}
            <div className="flex gap-2">
              <Button size="sm" disabled={busy} onClick={() => void submit()}>
                {busy ? "Freezing…" : `Freeze ${countSelected(selection)} objects`}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setCreating(false)}>
                Cancel
              </Button>
            </div>
          </CardContent>
        </Card>
      ) : null}

      {releases.length === 0 ? (
        <p className="text-sm text-muted-foreground">No releases yet.</p>
      ) : (
        <div className="divide-y rounded-md border">
          {releases.map((r) => (
            <button
              key={r.id}
              onClick={() => onOpen(r.id)}
              className="flex w-full items-center justify-between px-4 py-3 text-left hover:bg-accent/50"
            >
              <div className="flex items-center gap-2">
                <GitBranch className="h-4 w-4 text-muted-foreground" />
                <span className="font-medium">{r.name}</span>
                {r.description ? <span className="text-xs text-muted-foreground">{r.description}</span> : null}
              </div>
              <StatusBadge status={r.status} />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Targets
// --------------------------------------------------------------------------- //
function TargetsTab({
  targets,
  orgs,
  onChanged,
}: {
  targets: PromotionTarget[];
  orgs: { id: string; name: string }[];
  onChanged: () => Promise<void>;
}) {
  const [kind, setKind] = useState<TargetKind>("local_org");
  const [name, setName] = useState("");
  const [orgId, setOrgId] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [remoteOrgId, setRemoteOrgId] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);

  function reset() {
    setName("");
    setOrgId("");
    setBaseUrl("");
    setRemoteOrgId("");
    setApiKey("");
  }

  async function add() {
    if (!name.trim()) return toast.error("Name the target.");
    if (kind === "local_org" && !orgId) return toast.error("Choose an organization.");
    if (kind === "remote_instance" && (!baseUrl.trim() || !apiKey.trim() || !remoteOrgId.trim()))
      return toast.error("A remote target needs a base URL, remote org id, and API key.");
    setBusy(true);
    try {
      await createTarget(
        kind === "local_org"
          ? { name: name.trim(), kind, target_org_id: orgId }
          : {
              name: name.trim(),
              kind,
              base_url: baseUrl.trim(),
              remote_org_id: remoteOrgId.trim(),
              api_key: apiKey.trim(),
            },
      );
      toast.success("Target added");
      reset();
      await onChanged();
    } catch (error) {
      toast.error("Could not add target", { description: getApiErrorMessage(error, "") });
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: string) {
    try {
      await deleteTarget(id);
      await onChanged();
    } catch (error) {
      toast.error("Could not remove target", { description: getApiErrorMessage(error, "") });
    }
  }

  async function test(id: string) {
    const toastId = toast.loading("Testing connection…");
    try {
      const result = await testTarget(id);
      if (result.ok) {
        toast.success("Connection OK", {
          id: toastId,
          description: `Remote speaks bundle format v${result.remote_bundle_format_version ?? "?"}.`,
        });
      } else {
        toast.error("Connection failed", { id: toastId, description: result.error ?? "Unknown error" });
      }
    } catch (error) {
      toast.error("Connection failed", { id: toastId, description: getApiErrorMessage(error, "") });
    }
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Add a target</CardTitle>
          <CardDescription>
            Promote to another organization in this deployment, or to a remote KM2 instance over the network.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex gap-2">
            {(["local_org", "remote_instance"] as const).map((k) => (
              <button
                key={k}
                type="button"
                aria-pressed={kind === k}
                onClick={() => setKind(k)}
                className={cn(
                  "rounded-md border px-3 py-1.5 text-sm transition-colors",
                  kind === k ? "border-primary bg-accent" : "border-border hover:bg-accent/50",
                )}
              >
                {k === "local_org" ? "Local org" : "Remote instance"}
              </button>
            ))}
          </div>
          <div className="flex flex-wrap items-end gap-2">
            <Input placeholder="Target name (e.g. Staging)" value={name} onChange={(e) => setName(e.target.value)} className="max-w-xs" />
            {kind === "local_org" ? (
              <select
                value={orgId}
                onChange={(e) => setOrgId(e.target.value)}
                className="rounded-md border bg-background px-3 py-2 text-sm"
              >
                <option value="">Choose an organization…</option>
                {orgs.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.name}
                  </option>
                ))}
              </select>
            ) : (
              <>
                <Input placeholder="https://staging.example.com" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} className="max-w-xs" />
                <Input placeholder="Remote org id (UUID)" value={remoteOrgId} onChange={(e) => setRemoteOrgId(e.target.value)} className="max-w-xs" />
                <Input placeholder="API key (km2_…)" type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} className="max-w-xs" />
              </>
            )}
            <Button size="sm" disabled={busy} onClick={() => void add()}>
              <Plus className="mr-1 h-4 w-4" /> Add
            </Button>
          </div>
          {kind === "local_org" && orgs.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              You need admin rights on another organization to register a local target.
            </p>
          ) : null}
          {kind === "remote_instance" ? (
            <p className="text-xs text-muted-foreground">
              The remote instance needs an API key with the <span className="font-mono">config:write</span> scope.
              The key is stored write-only and never shown again.
            </p>
          ) : null}
        </CardContent>
      </Card>

      {targets.length === 0 ? (
        <p className="text-sm text-muted-foreground">No targets registered.</p>
      ) : (
        <div className="divide-y rounded-md border">
          {targets.map((t) => (
            <div key={t.id} className="flex items-center justify-between px-4 py-3">
              <div className="flex items-center gap-2">
                {t.kind === "local_org" ? (
                  <Rocket className="h-4 w-4 text-sky-500" />
                ) : (
                  <Server className="h-4 w-4 text-purple-500" />
                )}
                <span className="font-medium">{t.name}</span>
                <span className="rounded-full bg-muted px-2 py-0.5 text-xs capitalize">
                  {t.kind.replace(/_/g, " ")}
                </span>
                {t.base_url ? <span className="text-xs text-muted-foreground">{t.base_url}</span> : null}
              </div>
              <div className="flex items-center gap-1">
                {t.kind === "remote_instance" ? (
                  <Button size="sm" variant="ghost" onClick={() => void test(t.id)}>
                    Test
                  </Button>
                ) : null}
                <Button size="sm" variant="ghost" onClick={() => void remove(t.id)}>
                  <Trash2 className="h-4 w-4" />
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
