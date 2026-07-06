"use client";

import { Database, Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  createEntity,
  deleteEntity,
  listEntities,
  type EntityDefinition,
} from "@/lib/api/entities";
import { getApiErrorMessage } from "@/lib/api/errors";

/** Derive a valid slug (^[a-z][a-z0-9_]*$) from a display name. */
function slugify(name: string): string {
  const s = name
    .toLowerCase()
    .replace(/[^a-z0-9_]+/g, "_")
    .replace(/^_+|_+$/g, "");
  return /^[a-z]/.test(s) ? s : `e_${s}`;
}

export default function EntitiesPage() {
  const [items, setItems] = useState<EntityDefinition[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      setItems(await listEntities());
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to load entities"));
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    const name = newName.trim();
    if (!name || isSubmitting) return;
    setIsSubmitting(true);
    setError(null);
    try {
      await createEntity({ name, slug: slugify(name) });
      setNewName("");
      await load();
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Create failed"));
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleDelete = async (entity: EntityDefinition) => {
    if (!confirm(`Delete entity "${entity.name}" and its table? This cannot be undone.`)) return;
    try {
      await deleteEntity(entity.id);
      await load();
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Delete failed"));
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Entities</h1>
        <p className="text-sm text-muted-foreground">
          Define custom data types with fields and relationships. Each entity is backed by its own
          database table.
        </p>
      </div>

      <Card>
        <CardContent className="space-y-4 pt-6">
          <form onSubmit={handleCreate} className="flex gap-2">
            <Input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="New entity name (e.g. Customer)…"
              disabled={isSubmitting}
            />
            <Button type="submit" disabled={isSubmitting || !newName.trim()}>
              <Plus className="h-4 w-4" />
              Create
            </Button>
          </form>

          {error ? <p className="text-sm text-destructive">{error}</p> : null}

          {isLoading ? (
            <Skeleton className="h-10 w-full" />
          ) : items.length > 0 ? (
            <ul className="divide-y rounded-md border">
              {items.map((entity) => (
                <li key={entity.id} className="flex items-center gap-3 px-3 py-2">
                  <Database className="h-4 w-4 text-muted-foreground" />
                  <Link
                    href={`/entities/${entity.slug}`}
                    className="flex-1 text-sm font-medium hover:underline"
                  >
                    {entity.name}
                    <span className="ml-2 text-xs text-muted-foreground">
                      {entity.fields.length} field{entity.fields.length === 1 ? "" : "s"}
                    </span>
                  </Link>
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => void handleDelete(entity)}
                    aria-label={`Delete ${entity.name}`}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">No entities yet. Create one above.</p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
