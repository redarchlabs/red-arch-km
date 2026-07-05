"use client";

import { Check, Pencil, Plus, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useOrg } from "@/context/OrgContext";
import { getApiErrorMessage } from "@/lib/api/errors";
import { createOrg, deleteOrg, listOrgs, updateOrg } from "@/lib/api/orgs";
import type { Org } from "@/types";

export function OrgManager() {
  const { refresh } = useOrg();
  const [orgs, setOrgs] = useState<Org[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");

  const [deleteTarget, setDeleteTarget] = useState<Org | null>(null);
  const [deleteConfirmText, setDeleteConfirmText] = useState("");

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      setOrgs(await listOrgs());
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to load organizations"));
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
      await createOrg({ name, description: newDescription.trim() || null });
      setNewName("");
      setNewDescription("");
      await load();
      await refresh(); // keep the org switcher in sync
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Create failed"));
    } finally {
      setIsSubmitting(false);
    }
  };

  const saveEdit = async (org: Org) => {
    const trimmed = editName.trim();
    if (!trimmed || trimmed === org.name) {
      setEditingId(null);
      return;
    }
    try {
      await updateOrg(org.id, { name: trimmed });
      setEditingId(null);
      await load();
      await refresh();
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Rename failed"));
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget || deleteConfirmText !== deleteTarget.name) return;
    setIsSubmitting(true);
    setError(null);
    try {
      await deleteOrg(deleteTarget.id);
      setDeleteTarget(null);
      setDeleteConfirmText("");
      await load();
      await refresh();
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Delete failed"));
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <Card>
      <CardContent className="space-y-4 pt-6">
        <div>
          <h2 className="text-lg font-semibold">Organizations</h2>
          <p className="text-sm text-muted-foreground">{orgs.length} organizations</p>
        </div>

        <form onSubmit={handleCreate} className="flex flex-wrap gap-2">
          <Input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="New organization name…"
            disabled={isSubmitting}
            className="max-w-xs"
          />
          <Input
            value={newDescription}
            onChange={(e) => setNewDescription(e.target.value)}
            placeholder="Description (optional)"
            disabled={isSubmitting}
            className="max-w-sm"
          />
          <Button type="submit" disabled={isSubmitting || !newName.trim()}>
            <Plus className="h-4 w-4" />
            Add
          </Button>
        </form>

        {error ? <p className="text-sm text-destructive">{error}</p> : null}

        {isLoading ? (
          <Skeleton className="h-10 w-full" />
        ) : orgs.length > 0 ? (
          <ul className="divide-y rounded-md border">
            {orgs.map((org) => {
              const isEditing = org.id === editingId;
              return (
                <li key={org.id} className="flex items-center gap-2 px-3 py-2">
                  {isEditing ? (
                    <>
                      <Input
                        value={editName}
                        onChange={(e) => setEditName(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") void saveEdit(org);
                          if (e.key === "Escape") setEditingId(null);
                        }}
                        autoFocus
                        className="h-8"
                      />
                      <Button variant="ghost" size="icon" onClick={() => void saveEdit(org)} aria-label="Save">
                        <Check className="h-4 w-4" />
                      </Button>
                      <Button variant="ghost" size="icon" onClick={() => setEditingId(null)} aria-label="Cancel edit">
                        <X className="h-4 w-4" />
                      </Button>
                    </>
                  ) : (
                    <>
                      <div className="flex flex-1 flex-col">
                        <span className="text-sm font-medium">{org.name}</span>
                        {org.description ? (
                          <span className="text-xs text-muted-foreground">{org.description}</span>
                        ) : null}
                      </div>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => {
                          setEditingId(org.id);
                          setEditName(org.name);
                        }}
                        aria-label={`Rename ${org.name}`}
                      >
                        <Pencil className="h-4 w-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => {
                          setDeleteTarget(org);
                          setDeleteConfirmText("");
                        }}
                        aria-label={`Delete ${org.name}`}
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
          <p className="text-sm text-muted-foreground">No organizations yet.</p>
        )}

        <Dialog open={deleteTarget !== null} onClose={() => setDeleteTarget(null)}>
          <DialogHeader>
            <DialogTitle>Delete {deleteTarget?.name}?</DialogTitle>
            <DialogDescription>
              This permanently deletes the organization, its memberships, documents, and all
              indexed knowledge (vectors and graph data). This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <p className="text-sm">
              Type <span className="font-mono font-semibold">{deleteTarget?.name}</span> to confirm.
            </p>
            <Input
              value={deleteConfirmText}
              onChange={(e) => setDeleteConfirmText(e.target.value)}
              placeholder={deleteTarget?.name}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={isSubmitting || deleteConfirmText !== deleteTarget?.name}
              onClick={() => void handleDelete()}
            >
              Delete organization
            </Button>
          </DialogFooter>
        </Dialog>
      </CardContent>
    </Card>
  );
}
