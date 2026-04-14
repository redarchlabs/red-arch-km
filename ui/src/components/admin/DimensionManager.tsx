"use client";

import { Check, Pencil, Plus, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ADMIN_LIST_PAGE_SIZE,
  createDimension,
  deleteDimension,
  listDimensions,
  updateDimension,
  type Dimension,
  type DimensionKind,
} from "@/lib/api/dimensions";

interface DimensionManagerProps {
  kind: DimensionKind;
  label: string;
}

export function DimensionManager({ kind, label }: DimensionManagerProps) {
  const [items, setItems] = useState<Dimension[]>([]);
  const [total, setTotal] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const page = await listDimensions(kind);
      setItems(page.items);
      setTotal(page.total);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load");
    } finally {
      setIsLoading(false);
    }
  }, [kind]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    const name = newName.trim();
    if (!name || isSubmitting) return;
    setIsSubmitting(true);
    try {
      await createDimension(kind, { name });
      setNewName("");
      await load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Create failed");
    } finally {
      setIsSubmitting(false);
    }
  };

  const startEdit = (item: Dimension) => {
    setEditingId(item.id);
    setEditName(item.name);
    setError(null);
  };

  const cancelEdit = () => {
    setEditingId(null);
    setEditName("");
  };

  const saveEdit = async (item: Dimension) => {
    const trimmed = editName.trim();
    if (!trimmed || trimmed === item.name) {
      cancelEdit();
      return;
    }
    try {
      await updateDimension(kind, item.id, { name: trimmed });
      cancelEdit();
      await load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Update failed");
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await deleteDimension(kind, id);
      await load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Delete failed");
    }
  };

  return (
    <Card>
      <CardContent className="space-y-4 pt-6">
        <div>
          <h2 className="text-lg font-semibold">{label}</h2>
          <p className="text-sm text-muted-foreground">
            {total} {label.toLowerCase()}
          </p>
          {total > items.length ? (
            <p className="text-xs text-amber-600 dark:text-amber-500">
              Showing first {items.length} of {total}. The admin UI paginates at{" "}
              {ADMIN_LIST_PAGE_SIZE} — delete unused entries or contact an
              engineer to add pagination here.
            </p>
          ) : null}
        </div>

        <form onSubmit={handleCreate} className="flex gap-2">
          <Input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder={`New ${label.toLowerCase().replace(/s$/, "")}…`}
            disabled={isSubmitting}
          />
          <Button type="submit" disabled={isSubmitting || !newName.trim()}>
            <Plus className="h-4 w-4" />
            Add
          </Button>
        </form>

        {error ? <p className="text-sm text-destructive">{error}</p> : null}

        {isLoading ? (
          <Skeleton className="h-10 w-full" />
        ) : items.length > 0 ? (
          <ul className="divide-y rounded-md border">
            {items.map((item) => {
              const isEditing = item.id === editingId;
              return (
                <li key={item.id} className="flex items-center gap-2 px-3 py-2">
                  {isEditing ? (
                    <>
                      <Input
                        value={editName}
                        onChange={(e) => setEditName(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") void saveEdit(item);
                          if (e.key === "Escape") cancelEdit();
                        }}
                        autoFocus
                        className="h-8"
                      />
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => void saveEdit(item)}
                        aria-label="Save"
                      >
                        <Check className="h-4 w-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={cancelEdit}
                        aria-label="Cancel edit"
                      >
                        <X className="h-4 w-4" />
                      </Button>
                    </>
                  ) : (
                    <>
                      <div className="flex flex-1 items-center gap-2">
                        <span className="text-sm">{item.name}</span>
                        <Badge variant="outline">#{item.permission_number}</Badge>
                      </div>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => startEdit(item)}
                        aria-label={`Edit ${item.name}`}
                      >
                        <Pencil className="h-4 w-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => void handleDelete(item.id)}
                        aria-label={`Delete ${item.name}`}
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </>
                  )}
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">No {label.toLowerCase()} yet.</p>
        )}
      </CardContent>
    </Card>
  );
}
