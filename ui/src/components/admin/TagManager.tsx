"use client";

import { Plus, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { createTag, deleteTag, listTags } from "@/lib/api/tags";
import type { Tag } from "@/types";

export function TagManager() {
  const [tags, setTags] = useState<Tag[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      setTags(await listTags());
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
            {tags.length} tag{tags.length === 1 ? "" : "s"}
          </p>
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
            {tags.map((tag) => (
              <li key={tag.id} className="flex items-center justify-between px-3 py-2">
                <span className="text-sm">{tag.name}</span>
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => void handleDelete(tag.id)}
                  aria-label={`Delete ${tag.name}`}
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">No tags yet.</p>
        )}
      </CardContent>
    </Card>
  );
}
