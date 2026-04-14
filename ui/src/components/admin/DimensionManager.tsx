"use client";

import { Plus, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  createDimension,
  deleteDimension,
  listDimensions,
  type Dimension,
  type DimensionKind,
} from "@/lib/api/dimensions";

interface DimensionManagerProps {
  kind: DimensionKind;
  label: string;
}

export function DimensionManager({ kind, label }: DimensionManagerProps) {
  const [items, setItems] = useState<Dimension[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      setItems(await listDimensions(kind));
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
    if (!newName.trim() || isSubmitting) return;
    setIsSubmitting(true);
    try {
      await createDimension(kind, { name: newName.trim() });
      setNewName("");
      await load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Create failed");
    } finally {
      setIsSubmitting(false);
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
            {items.length} {label.toLowerCase()}
          </p>
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
            {items.map((item) => (
              <li key={item.id} className="flex items-center justify-between px-3 py-2">
                <div className="flex items-center gap-2">
                  <span className="text-sm">{item.name}</span>
                  <Badge variant="outline">#{item.permission_number}</Badge>
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => void handleDelete(item.id)}
                  aria-label={`Delete ${item.name}`}
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">No {label.toLowerCase()} yet.</p>
        )}
      </CardContent>
    </Card>
  );
}
