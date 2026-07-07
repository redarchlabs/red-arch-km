"use client";

import { ArrowLeft, KeyRound, Pencil, Plug, Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  buildConnectionCreate,
  buildConnectionUpdate,
  CONNECTION_AUTH_LABELS,
  CONNECTION_AUTH_TYPES,
  connectionToForm,
  createConnection,
  deleteConnection,
  EMPTY_CONNECTION_FORM,
  listConnections,
  updateConnection,
  type Connection,
  type ConnectionFormState,
} from "@/lib/api/connections";
import { getApiErrorMessage } from "@/lib/api/errors";

const selectClass = "h-9 w-full rounded-md border bg-background px-2 text-sm";

export default function ConnectionsPage() {
  const [items, setItems] = useState<Connection[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // `null` = dialog closed; otherwise the connection being edited, or `create`.
  const [editing, setEditing] = useState<Connection | "create" | null>(null);

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      setItems(await listConnections());
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to load connections"));
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const handleDelete = async (conn: Connection) => {
    if (!confirm(`Delete connection "${conn.name}"? Tasks using it will fail until reconfigured.`)) return;
    setError(null);
    try {
      await deleteConnection(conn.id);
      await load();
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to delete connection"));
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Link href="/workflows" className="text-muted-foreground hover:text-foreground">
          <ArrowLeft className="h-5 w-5" />
        </Link>
        <div className="flex-1">
          <h1 className="text-2xl font-semibold">Connections</h1>
          <p className="text-sm text-muted-foreground">
            Reusable API credentials the &ldquo;Call a connected API&rdquo; task authenticates through.
            Secrets are encrypted and never shown again.
          </p>
        </div>
        <Button size="sm" onClick={() => setEditing("create")}>
          <Plus className="h-4 w-4" />
          New connection
        </Button>
      </div>

      {error ? <p className="text-sm text-destructive">{error}</p> : null}

      <Card>
        <CardContent className="pt-6">
          {isLoading ? (
            <Skeleton className="h-24 w-full" />
          ) : items.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No connections yet. Create one to call an authenticated API from a workflow.
            </p>
          ) : (
            <ul className="divide-y rounded-md border">
              {items.map((conn) => (
                <li key={conn.id} className="flex items-center gap-3 px-3 py-2">
                  <Plug className="h-4 w-4 text-muted-foreground" />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium">{conn.name}</div>
                    <div className="truncate text-xs text-muted-foreground">
                      {conn.base_url || "no base URL"} · {CONNECTION_AUTH_LABELS[conn.auth_type]}
                      {conn.has_secret ? (
                        <span className="ml-1 inline-flex items-center gap-0.5 text-emerald-600 dark:text-emerald-400">
                          <KeyRound className="h-3 w-3" /> secret set
                        </span>
                      ) : null}
                    </div>
                  </div>
                  <Button variant="ghost" size="icon" onClick={() => setEditing(conn)} aria-label={`Edit ${conn.name}`}>
                    <Pencil className="h-4 w-4" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => void handleDelete(conn)}
                    aria-label={`Delete ${conn.name}`}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      {editing !== null ? (
        <ConnectionDialog
          connection={editing === "create" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            setEditing(null);
            await load();
          }}
        />
      ) : null}
    </div>
  );
}

function ConnectionDialog({
  connection,
  onClose,
  onSaved,
}: {
  /** The connection to edit, or null to create a new one. */
  connection: Connection | null;
  onClose: () => void;
  onSaved: () => void | Promise<void>;
}) {
  const [form, setForm] = useState<ConnectionFormState>(
    connection ? connectionToForm(connection) : EMPTY_CONNECTION_FORM,
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const set = <K extends keyof ConnectionFormState>(key: K, value: ConnectionFormState[K]) =>
    setForm((f) => ({ ...f, [key]: value }));
  const setConfig = (key: string, value: string) =>
    setForm((f) => ({ ...f, config: { ...f.config, [key]: value } }));

  // Show the placeholder only for a stored secret the operator hasn't replaced.
  const keepsStoredSecret = Boolean(connection?.has_secret) && !form.secretDirty;
  const usesSecret = form.auth_type !== "none";
  const secretLabel =
    form.auth_type === "basic" ? "Password" : form.auth_type === "api_key" ? "API key value" : "Bearer token";

  const save = async () => {
    if (!form.name.trim() || saving) return;
    setSaving(true);
    setError(null);
    try {
      if (connection) {
        await updateConnection(connection.id, buildConnectionUpdate(form));
      } else {
        await createConnection(buildConnectionCreate(form));
      }
      await onSaved();
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to save connection"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open onClose={onClose}>
      <DialogHeader>
        <DialogTitle>{connection ? "Edit connection" : "New connection"}</DialogTitle>
      </DialogHeader>

      <div className="space-y-3">
        <div>
          <label className="text-xs font-medium text-muted-foreground">Name</label>
          <Input
            value={form.name}
            onChange={(e) => set("name", e.target.value)}
            placeholder="Stripe API"
            className="mt-1"
          />
        </div>

        <div>
          <label className="text-xs font-medium text-muted-foreground">Base URL</label>
          <Input
            value={form.base_url}
            onChange={(e) => set("base_url", e.target.value)}
            placeholder="https://api.stripe.com"
            className="mt-1"
          />
          <p className="mt-1 text-xs text-muted-foreground">
            A request&rsquo;s path is appended to this. The host must be allow-listed.
          </p>
        </div>

        <div>
          <label className="text-xs font-medium text-muted-foreground">Authentication</label>
          <select
            value={form.auth_type}
            onChange={(e) => set("auth_type", e.target.value as ConnectionFormState["auth_type"])}
            className={`${selectClass} mt-1`}
          >
            {CONNECTION_AUTH_TYPES.map((t) => (
              <option key={t} value={t}>
                {CONNECTION_AUTH_LABELS[t]}
              </option>
            ))}
          </select>
        </div>

        {form.auth_type === "api_key" ? (
          <div>
            <label className="text-xs font-medium text-muted-foreground">Header name</label>
            <Input
              value={String(form.config.header ?? "")}
              onChange={(e) => setConfig("header", e.target.value)}
              placeholder="X-Api-Key"
              className="mt-1"
            />
          </div>
        ) : null}

        {form.auth_type === "basic" ? (
          <div>
            <label className="text-xs font-medium text-muted-foreground">Username</label>
            <Input
              value={String(form.config.username ?? "")}
              onChange={(e) => setConfig("username", e.target.value)}
              placeholder="api_user"
              className="mt-1"
            />
          </div>
        ) : null}

        {usesSecret ? (
          <div>
            <label className="text-xs font-medium text-muted-foreground">{secretLabel}</label>
            <Input
              type="password"
              value={form.secret}
              onChange={(e) => setForm((f) => ({ ...f, secret: e.target.value, secretDirty: true }))}
              placeholder={keepsStoredSecret ? "•••• set — leave blank to keep" : "Enter the secret"}
              className="mt-1"
            />
            {keepsStoredSecret ? (
              <p className="mt-1 text-xs text-muted-foreground">
                A secret is stored. Leave this blank to keep it, or type a new one to replace it.
              </p>
            ) : null}
          </div>
        ) : null}

        {error ? <p className="text-sm text-destructive">{error}</p> : null}
      </div>

      <DialogFooter>
        <Button variant="outline" onClick={onClose} disabled={saving}>
          Cancel
        </Button>
        <Button onClick={() => void save()} disabled={saving || !form.name.trim()}>
          {saving ? "Saving…" : connection ? "Save changes" : "Create"}
        </Button>
      </DialogFooter>
    </Dialog>
  );
}
