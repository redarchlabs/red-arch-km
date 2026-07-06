"use client";

import { ClipboardList, Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { listEntities, type EntityDefinition } from "@/lib/api/entities";
import { getApiErrorMessage } from "@/lib/api/errors";
import { createForm, deleteForm, listForms, type Form } from "@/lib/api/forms";

function slugify(name: string): string {
  const s = name.toLowerCase().replace(/[^a-z0-9_]+/g, "-").replace(/^-+|-+$/g, "");
  return s || "form";
}

export default function FormsPage() {
  const [forms, setForms] = useState<Form[]>([]);
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
      const [f, e] = await Promise.all([listForms(), listEntities()]);
      setForms(f);
      setEntities(e);
      if (!entityId && e.length > 0) setEntityId(e[0].id);
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to load forms"));
    } finally {
      setLoading(false);
    }
  }, [entityId]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || !entityId) return;
    setCreating(true);
    setError(null);
    try {
      await createForm({ name: name.trim(), slug: slugify(name), entity_definition_id: entityId });
      setName("");
      await load();
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to create form"));
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (form: Form) => {
    if (!confirm(`Delete form "${form.name}"? Existing links will stop working.`)) return;
    setError(null);
    try {
      await deleteForm(form.id);
      await load();
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to delete form"));
    }
  };

  const entityName = (id: string) => entities.find((e) => e.id === id)?.name ?? "—";

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Forms</h1>
        <p className="text-sm text-muted-foreground">
          Build intake forms that collect data into an entity, then send a secure link to fill one in.
        </p>
      </div>

      {error ? <p className="text-sm text-destructive">{error}</p> : null}

      <Card>
        <CardContent className="pt-6">
          <form onSubmit={handleCreate} className="flex flex-wrap items-end gap-2">
            <div className="flex-1 min-w-48">
              <label className="mb-1 block text-sm font-medium">New form</label>
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Form name…" />
            </div>
            <select
              value={entityId}
              onChange={(e) => setEntityId(e.target.value)}
              className="h-9 rounded-md border bg-background px-2 text-sm"
            >
              {entities.length === 0 ? <option value="">No entities</option> : null}
              {entities.map((e) => (
                <option key={e.id} value={e.id}>
                  {e.name}
                </option>
              ))}
            </select>
            <Button type="submit" disabled={creating || !name.trim() || !entityId}>
              <Plus className="h-4 w-4" />
              Create
            </Button>
          </form>
        </CardContent>
      </Card>

      {loading ? (
        <Skeleton className="h-40 w-full" />
      ) : forms.length === 0 ? (
        <p className="text-sm text-muted-foreground">No forms yet. Create one above.</p>
      ) : (
        <ul className="divide-y rounded-md border">
          {forms.map((form) => (
            <li key={form.id} className="flex items-center gap-3 px-4 py-3">
              <ClipboardList className="h-4 w-4 text-muted-foreground" />
              <Link href={`/forms/${form.id}`} className="flex-1 font-medium hover:underline">
                {form.name}
              </Link>
              <span className="text-xs text-muted-foreground">{entityName(form.entity_definition_id)}</span>
              {!form.is_active ? <span className="text-xs text-muted-foreground">(inactive)</span> : null}
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="h-7 w-7 text-muted-foreground hover:text-destructive"
                onClick={() => void handleDelete(form)}
                aria-label={`Delete ${form.name}`}
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
