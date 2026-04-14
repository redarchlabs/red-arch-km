"use client";

import { Check, Pencil, Plus, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ADMIN_LIST_PAGE_SIZE,
  createTag,
  deleteTag,
  listTags,
  updateTag,
} from "@/lib/api/tags";
import type { Tag } from "@/types";

export function TagManager() {
  const [tags, setTags] = useState<Tag[]>([]);
  const [total, setTotal] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const page = await listTags();
      setTags(page.items);
      setTotal(page.total);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load tags");
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
    if (!name || submitting) return;
    setSubmitting(true);
    try {
      await createTag(name);
      setNewName("");
      await load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Create failed");
    } finally {
      setSubmitting(false);
    }
  };

  const startEdit = (tag: Tag) => {
    setEditingId(tag.id);
    setEditName(tag.name);
    setError(null);
  };

  const cancelEdit = () => {
    setEditingId(null);
    setEditName("");
  };

  const saveEdit = async (tag: Tag) => {
    const trimmed = editName.trim();
    if (!trimmed || trimmed === tag.name) {
      cancelEdit();
      return;
    }
    try {
      await updateTag(tag.id, trimmed);
      cancelEdit();
      await load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Update failed");
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await deleteTag(id);
      await load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Delete failed");
    }
  };

  return (
    <Card>
      <CardContent className="space-y-4 pt-6">
        <div>
          <h2 className="text-lg font-semibold">Tags</h2>
          <p className="text-sm text-muted-foreground">
            {total} tag{total === 1 ? "" : "s"}
          </p>
          {total > tags.length ? (
            <p className="text-xs text-amber-600 dark:text-amber-500">
              Showing first {tags.length} of {total}. The admin UI paginates at{" "}
              {ADMIN_LIST_PAGE_SIZE} — delete unused tags or contact an engineer
              to add pagination here.
            </p>
          ) : null}
        </div>

        <form onSubmit={handleCreate} className="flex gap-2">
          <Input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="New tag…"
            disabled={submitting}
          />
          <Button type="submit" disabled={submitting || !newName.trim()}>
            <Plus className="h-4 w-4" />
            Add
          </Button>
        </form>

        {error ? <p className="text-sm text-destructive">{error}</p> : null}

        {isLoading ? (
          <Skeleton className="h-10 w-full" />
        ) : tags.length > 0 ? (
          <ul className="divide-y rounded-md border">
            {tags.map((tag) => {
              const isEditing = tag.id === editingId;
              return (
                <li key={tag.id} className="flex items-center gap-2 px-3 py-2">
                  {isEditing ? (
                    <>
                      <Input
                        value={editName}
                        onChange={(e) => setEditName(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") void saveEdit(tag);
                          if (e.key === "Escape") cancelEdit();
                        }}
                        autoFocus
                        className="h-8"
                      />
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => void saveEdit(tag)}
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
                      <span className="flex-1 text-sm">{tag.name}</span>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => startEdit(tag)}
                        aria-label={`Edit ${tag.name}`}
                      >
                        <Pencil className="h-4 w-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => void handleDelete(tag.id)}
                        aria-label={`Delete ${tag.name}`}
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
          <p className="text-sm text-muted-foreground">No tags yet.</p>
        )}
      </CardContent>
    </Card>
  );
}
