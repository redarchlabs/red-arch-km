"use client";

import { ArrowLeft, Copy, Plus, Trash2, Webhook } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { getApiErrorMessage } from "@/lib/api/errors";
import {
  createInboundEndpoint,
  deleteInboundEndpoint,
  listInboundEndpoints,
  type InboundEndpoint,
  type InboundEndpointCreated,
} from "@/lib/api/inboundEndpoints";
import { listWorkflows, type Workflow } from "@/lib/api/workflows";

const selectClass = "h-9 rounded-md border bg-background px-2 text-sm";

export default function WebhooksPage() {
  const [items, setItems] = useState<InboundEndpoint[]>([]);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [workflowId, setWorkflowId] = useState("");
  const [creating, setCreating] = useState(false);
  // The plaintext token is returned once; hold it to show the copy dialog.
  const [created, setCreated] = useState<InboundEndpointCreated | null>(null);

  const workflowName = (id: string) => workflows.find((w) => w.id === id)?.name ?? "—";

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const [endpoints, wfs] = await Promise.all([listInboundEndpoints(), listWorkflows()]);
      setItems(endpoints);
      setWorkflows(wfs);
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to load webhooks"));
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || !workflowId || creating) return;
    setCreating(true);
    setError(null);
    try {
      const result = await createInboundEndpoint({ name: name.trim(), workflow_id: workflowId });
      setCreated(result); // show the one-time token dialog
      setName("");
      setWorkflowId("");
      await load();
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to create webhook"));
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (endpoint: InboundEndpoint) => {
    if (!confirm(`Delete webhook "${endpoint.name}"? Its URL will stop working immediately.`)) return;
    setError(null);
    try {
      await deleteInboundEndpoint(endpoint.id);
      await load();
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to delete webhook"));
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Link href="/workflows" className="text-muted-foreground hover:text-foreground">
          <ArrowLeft className="h-5 w-5" />
        </Link>
        <div className="flex-1">
          <h1 className="text-2xl font-semibold">Inbound webhooks</h1>
          <p className="text-sm text-muted-foreground">
            Public URLs that start a workflow run when called. The secret token is shown once at
            creation.
          </p>
        </div>
      </div>

      {error ? <p className="text-sm text-destructive">{error}</p> : null}

      <Card>
        <CardContent className="pt-6">
          <form onSubmit={handleCreate} className="flex flex-wrap items-end gap-2">
            <div className="min-w-48 flex-1">
              <label className="mb-1 block text-sm font-medium">New webhook</label>
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Endpoint name…" />
            </div>
            <select value={workflowId} onChange={(e) => setWorkflowId(e.target.value)} className={selectClass}>
              <option value="">Starts workflow…</option>
              {workflows.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name}
                </option>
              ))}
            </select>
            <Button type="submit" disabled={creating || !name.trim() || !workflowId}>
              <Plus className="h-4 w-4" />
              Create
            </Button>
          </form>
          {workflows.length === 0 ? (
            <p className="mt-2 text-xs text-amber-600 dark:text-amber-500">
              Create a workflow first — a webhook has to start something.
            </p>
          ) : null}
        </CardContent>
      </Card>

      {isLoading ? (
        <Skeleton className="h-24 w-full" />
      ) : items.length === 0 ? (
        <p className="text-sm text-muted-foreground">No inbound webhooks yet.</p>
      ) : (
        <ul className="divide-y rounded-md border">
          {items.map((endpoint) => (
            <li key={endpoint.id} className="flex items-center gap-3 px-4 py-3">
              <Webhook className="h-4 w-4 text-muted-foreground" />
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium">{endpoint.name}</div>
                <div className="truncate text-xs text-muted-foreground">
                  starts {workflowName(endpoint.workflow_id)}
                </div>
              </div>
              {!endpoint.enabled ? <span className="text-xs text-muted-foreground">(disabled)</span> : null}
              <Button
                variant="ghost"
                size="icon"
                onClick={() => void handleDelete(endpoint)}
                aria-label={`Delete ${endpoint.name}`}
              >
                <Trash2 className="h-4 w-4" />
              </Button>
            </li>
          ))}
        </ul>
      )}

      {created ? <TokenDialog created={created} onClose={() => setCreated(null)} /> : null}
    </div>
  );
}

function TokenDialog({ created, onClose }: { created: InboundEndpointCreated; onClose: () => void }) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard?.writeText(created.url);
      setCopied(true);
    } catch {
      // Clipboard may be unavailable (insecure context) — the URL is still shown to copy manually.
      setCopied(false);
    }
  };

  return (
    <Dialog open onClose={onClose}>
      <DialogHeader>
        <DialogTitle>Webhook created</DialogTitle>
        <DialogDescription>
          Copy this URL now — the secret token is part of it and won&rsquo;t be shown again.
        </DialogDescription>
      </DialogHeader>

      <div className="rounded-md border bg-muted/40 p-3">
        <p className="mb-1 text-xs font-medium text-muted-foreground">
          Callable URL for &ldquo;{created.name}&rdquo;
        </p>
        <div className="flex items-center gap-2">
          <code className="flex-1 break-all rounded bg-background px-2 py-1 text-xs">{created.url}</code>
          <Button
            type="button"
            variant="outline"
            size="icon"
            className="h-7 w-7 shrink-0"
            onClick={() => void copy()}
            aria-label="Copy webhook URL"
          >
            <Copy className="h-4 w-4" />
          </Button>
        </div>
        {copied ? <p className="mt-1 text-xs text-emerald-600 dark:text-emerald-400">Copied to clipboard.</p> : null}
      </div>

      <DialogFooter>
        <Button onClick={onClose}>Done</Button>
      </DialogFooter>
    </Dialog>
  );
}
