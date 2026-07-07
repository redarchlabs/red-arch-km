"use client";

import { LayoutDashboard, Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { listEntities, type EntityDefinition } from "@/lib/api/entities";
import { getApiErrorMessage } from "@/lib/api/errors";
import { createView, deleteView, listViews, type View } from "@/lib/api/views";

function slugify(name: string): string {
  const s = name.toLowerCase().replace(/[^a-z0-9_]+/g, "-").replace(/^-+|-+$/g, "");
  return s || "view";
}

export default function ViewsPage() {
  const [views, setViews] = useState<View[]>([]);
  const [entities, setEntities] = useState<EntityDefinition[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [entityId, setEntityId] = useState("");
  const [creating, setCreating] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [v, e] = await Promise.all([listViews(), listEntities().catch(() => [])]);
      setViews(v);
      setEntities(e);
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to load views"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    setCreating(true);
    setError(null);
    try {
      await createView({
        name: name.trim(),
        slug: slugify(name),
        entity_definition_id: entityId || null,
      });
      setName("");
      await load();
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to create view"));
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (view: View) => {
    if (!confirm(`Delete view "${view.name}"?`)) return;
    try {
      await deleteView(view.id);
      await load();
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to delete view"));
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Views</h1>
        <p className="text-sm text-muted-foreground">
          Compose screens from forms, action buttons, and layout — bound to an entity record or standalone.
        </p>
      </div>

      {error ? <p className="text-sm text-destructive">{error}</p> : null}

      <Card>
        <CardContent className="pt-6">
          <form onSubmit={handleCreate} className="flex flex-wrap items-end gap-2">
            <div className="min-w-48 flex-1">
              <label className="mb-1 block text-sm font-medium">New view</label>
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="View name…" />
            </div>
            <select
              value={entityId}
              onChange={(e) => setEntityId(e.target.value)}
              className="h-9 rounded-md border bg-background px-2 text-sm"
            >
              <option value="">Standalone (no entity)</option>
              {entities.map((e) => (
                <option key={e.id} value={e.id}>
                  {e.name}
                </option>
              ))}
            </select>
            <Button type="submit" disabled={creating || !name.trim()}>
              <Plus className="h-4 w-4" />
              Create
            </Button>
          </form>
        </CardContent>
      </Card>

      {loading ? (
        <Skeleton className="h-40 w-full" />
      ) : views.length === 0 ? (
        <p className="text-sm text-muted-foreground">No views yet. Create one above.</p>
      ) : (
        <ul className="divide-y rounded-md border">
          {views.map((view) => (
            <li key={view.id} className="flex items-center gap-3 px-4 py-3">
              <LayoutDashboard className="h-4 w-4 text-muted-foreground" />
              <Link href={`/views/${view.id}`} className="flex-1 font-medium hover:underline">
                {view.name}
              </Link>
              <Link href={`/views/${view.id}/view`} className="text-xs text-muted-foreground hover:underline">
                Open
              </Link>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="h-7 w-7 text-muted-foreground hover:text-destructive"
                onClick={() => void handleDelete(view)}
                aria-label={`Delete ${view.name}`}
              >
                <Trash2 className="h-4 w-4" />
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
