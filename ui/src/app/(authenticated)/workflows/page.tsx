"use client";

import { Activity, Pencil, Plus, Trash2, Workflow as WorkflowIcon } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { listEntities, type EntityDefinition } from "@/lib/api/entities";
import { getApiErrorMessage } from "@/lib/api/errors";
import {
  createWorkflow,
  deleteWorkflow,
  listWorkflows,
  updateWorkflow,
  type Workflow,
} from "@/lib/api/workflows";

const selectClass = "h-9 rounded-md border bg-background px-2 text-sm";

export default function WorkflowsPage() {
  const [items, setItems] = useState<Workflow[]>([]);
  const [entities, setEntities] = useState<EntityDefinition[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [entityId, setEntityId] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  const entityName = (id: string) => entities.find((e) => e.id === id)?.name ?? "—";

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const [wfs, ents] = await Promise.all([listWorkflows(), listEntities()]);
      setItems(wfs);
      setEntities(ents);
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to load workflows"));
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
    if (!name || !entityId || isSubmitting) return;
    setIsSubmitting(true);
    setError(null);
    try {
      await createWorkflow({ name, entity_definition_id: entityId });
      setNewName("");
      setEntityId("");
      await load();
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Create failed"));
    } finally {
      setIsSubmitting(false);
    }
  };

  const toggleEnabled = async (wf: Workflow) => {
    try {
      await updateWorkflow(wf.id, { enabled: !wf.enabled });
      await load();
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Update failed"));
    }
  };

  const handleDelete = async (wf: Workflow) => {
    if (!confirm(`Delete workflow "${wf.name}"?`)) return;
    try {
      await deleteWorkflow(wf.id);
      await load();
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Delete failed"));
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Workflows</h1>
        <p className="text-sm text-muted-foreground">
          Rules that fire when entity records change. Design them visually, test, then publish.
        </p>
      </div>

      <Card>
        <CardContent className="space-y-4 pt-6">
          <form onSubmit={handleCreate} className="flex flex-wrap gap-2">
            <Input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="New workflow name…"
              disabled={isSubmitting}
              className="max-w-xs"
            />
            <select
              value={entityId}
              onChange={(e) => setEntityId(e.target.value)}
              className={selectClass}
              disabled={isSubmitting}
            >
              <option value="">On entity…</option>
              {entities.map((ent) => (
                <option key={ent.id} value={ent.id}>
                  {ent.name}
                </option>
              ))}
            </select>
            <Button type="submit" disabled={isSubmitting || !newName.trim() || !entityId}>
              <Plus className="h-4 w-4" />
              Create
            </Button>
          </form>
          {entities.length === 0 ? (
            <p className="text-xs text-amber-600 dark:text-amber-500">
              Create an entity first — workflows fire on entity record changes.
            </p>
          ) : null}

          {error ? <p className="text-sm text-destructive">{error}</p> : null}

          {isLoading ? (
            <Skeleton className="h-10 w-full" />
          ) : items.length > 0 ? (
            <ul className="divide-y rounded-md border">
              {items.map((wf) => (
                <li key={wf.id} className="flex items-center gap-3 px-3 py-2">
                  <WorkflowIcon className="h-4 w-4 text-muted-foreground" />
                  <div className="flex-1">
                    <div className="text-sm font-medium">{wf.name}</div>
                    <div className="text-xs text-muted-foreground">on {entityName(wf.entity_definition_id)}</div>
                  </div>
                  <Badge variant={wf.enabled ? "default" : "outline"}>
                    {wf.enabled ? "enabled" : "disabled"}
                  </Badge>
                  <Button variant="ghost" size="sm" onClick={() => void toggleEnabled(wf)}>
                    {wf.enabled ? "Disable" : "Enable"}
                  </Button>
                  <Link href={`/workflows/${wf.id}/design`}>
                    <Button variant="ghost" size="icon" aria-label="Design">
                      <Pencil className="h-4 w-4" />
                    </Button>
                  </Link>
                  <Link href={`/workflows/${wf.id}/runs`}>
                    <Button variant="ghost" size="icon" aria-label="Runs">
                      <Activity className="h-4 w-4" />
                    </Button>
                  </Link>
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => void handleDelete(wf)}
                    aria-label={`Delete ${wf.name}`}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">No workflows yet.</p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
