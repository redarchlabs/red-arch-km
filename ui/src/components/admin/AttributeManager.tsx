"use client";

import { Check, Pencil, Plus, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { z } from "zod";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ADMIN_LIST_PAGE_SIZE,
  type AttributeDefinition,
  type AttributeType,
  createAttribute,
  deleteAttribute,
  listAttributes,
  updateAttribute,
} from "@/lib/api/attributes";

const createSchema = z.object({
  name: z.string().min(1).max(100),
  slug: z
    .string()
    .min(1)
    .max(100)
    .regex(/^[a-z][a-z0-9_]*$/, "slug must be lowercase [a-z0-9_] starting with a letter"),
  attribute_type: z.enum(["freeform", "picklist"]),
  picklist_options: z.array(z.string().min(1)),
  required: z.boolean(),
});

interface EditDraft {
  name: string;
  required: boolean;
  picklistRaw: string;
}

export function AttributeManager() {
  const [items, setItems] = useState<AttributeDefinition[]>([]);
  const [total, setTotal] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [attributeType, setAttributeType] = useState<AttributeType>("freeform");
  const [picklistRaw, setPicklistRaw] = useState("");
  const [required, setRequired] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const [editingId, setEditingId] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState<EditDraft | null>(null);

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const page = await listAttributes();
      setItems(page.items);
      setTotal(page.total);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load attributes");
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const reset = () => {
    setName("");
    setSlug("");
    setAttributeType("freeform");
    setPicklistRaw("");
    setRequired(false);
  };

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    const options =
      attributeType === "picklist"
        ? picklistRaw
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean)
        : [];

    const parsed = createSchema.safeParse({
      name,
      slug,
      attribute_type: attributeType,
      picklist_options: options,
      required,
    });
    if (!parsed.success) {
      setError(parsed.error.issues[0]?.message ?? "Invalid input");
      return;
    }
    if (attributeType === "picklist" && options.length === 0) {
      setError("Picklist attributes need at least one option (comma-separated)");
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      await createAttribute({
        name: parsed.data.name,
        slug: parsed.data.slug,
        attribute_type: parsed.data.attribute_type,
        picklist_options: options,
        required: parsed.data.required,
      });
      reset();
      await load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Create failed");
    } finally {
      setSubmitting(false);
    }
  };

  const startEdit = (attr: AttributeDefinition) => {
    setEditingId(attr.id);
    setEditDraft({
      name: attr.name,
      required: attr.required,
      picklistRaw: attr.picklist_options.join(", "),
    });
    setError(null);
  };

  const cancelEdit = () => {
    setEditingId(null);
    setEditDraft(null);
  };

  const saveEdit = async (attr: AttributeDefinition) => {
    if (!editDraft) return;
    const trimmedName = editDraft.name.trim();
    if (!trimmedName) {
      setError("Name cannot be empty");
      return;
    }

    let nextOptions: string[] | undefined;
    if (attr.attribute_type === "picklist") {
      nextOptions = editDraft.picklistRaw
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      if (nextOptions.length === 0) {
        setError("Picklist attributes need at least one option");
        return;
      }
    }

    try {
      await updateAttribute(attr.id, {
        name: trimmedName,
        required: editDraft.required,
        ...(nextOptions !== undefined ? { picklist_options: nextOptions } : {}),
      });
      cancelEdit();
      await load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Update failed");
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await deleteAttribute(id);
      await load();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Delete failed");
    }
  };

  return (
    <Card>
      <CardContent className="space-y-4 pt-6">
        <div>
          <h2 className="text-lg font-semibold">Document Attributes</h2>
          <p className="text-sm text-muted-foreground">
            Define metadata fields that can be attached to documents.
          </p>
          {total > items.length ? (
            <p className="text-xs text-amber-600 dark:text-amber-500">
              Showing first {items.length} of {total}. The admin UI paginates at{" "}
              {ADMIN_LIST_PAGE_SIZE} — delete unused attributes or contact an
              engineer to add pagination here.
            </p>
          ) : null}
        </div>

        <form onSubmit={handleCreate} className="space-y-3 rounded-md border p-3">
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Display name"
              disabled={submitting}
            />
            <Input
              value={slug}
              onChange={(e) => setSlug(e.target.value)}
              placeholder="slug_key"
              disabled={submitting}
            />
          </div>
          <div className="flex items-center gap-4 text-sm">
            <label className="flex items-center gap-2">
              <input
                type="radio"
                name="attr-type"
                checked={attributeType === "freeform"}
                onChange={() => setAttributeType("freeform")}
                disabled={submitting}
              />
              Freeform
            </label>
            <label className="flex items-center gap-2">
              <input
                type="radio"
                name="attr-type"
                checked={attributeType === "picklist"}
                onChange={() => setAttributeType("picklist")}
                disabled={submitting}
              />
              Picklist
            </label>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={required}
                onChange={(e) => setRequired(e.target.checked)}
                disabled={submitting}
              />
              Required
            </label>
          </div>
          {attributeType === "picklist" ? (
            <Input
              value={picklistRaw}
              onChange={(e) => setPicklistRaw(e.target.value)}
              placeholder="Option A, Option B, Option C"
              disabled={submitting}
            />
          ) : null}
          <Button type="submit" disabled={submitting || !name.trim() || !slug.trim()}>
            <Plus className="h-4 w-4" />
            Add attribute
          </Button>
        </form>

        {error ? <p className="text-sm text-destructive">{error}</p> : null}

        {isLoading ? (
          <Skeleton className="h-20 w-full" />
        ) : items.length > 0 ? (
          <ul className="divide-y rounded-md border">
            {items.map((attr) => {
              const isEditing = attr.id === editingId;
              if (isEditing && editDraft) {
                return (
                  <li key={attr.id} className="space-y-2 px-3 py-3">
                    <div className="flex items-center gap-2">
                      <Input
                        value={editDraft.name}
                        onChange={(e) =>
                          setEditDraft({ ...editDraft, name: e.target.value })
                        }
                        className="h-8 flex-1"
                        autoFocus
                      />
                      <Badge variant="outline">{attr.attribute_type}</Badge>
                    </div>
                    <p className="font-mono text-xs text-muted-foreground">{attr.slug}</p>
                    <label className="flex items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        checked={editDraft.required}
                        onChange={(e) =>
                          setEditDraft({ ...editDraft, required: e.target.checked })
                        }
                      />
                      Required
                    </label>
                    {attr.attribute_type === "picklist" ? (
                      <Input
                        value={editDraft.picklistRaw}
                        onChange={(e) =>
                          setEditDraft({ ...editDraft, picklistRaw: e.target.value })
                        }
                        placeholder="Option A, Option B, Option C"
                        className="h-8"
                      />
                    ) : null}
                    <div className="flex gap-2">
                      <Button
                        size="sm"
                        onClick={() => void saveEdit(attr)}
                        aria-label="Save"
                      >
                        <Check className="h-4 w-4" />
                        Save
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={cancelEdit}
                        aria-label="Cancel edit"
                      >
                        <X className="h-4 w-4" />
                        Cancel
                      </Button>
                    </div>
                  </li>
                );
              }
              return (
                <li key={attr.id} className="flex items-start justify-between gap-3 px-3 py-2">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="font-medium">{attr.name}</span>
                      <Badge variant="outline">{attr.attribute_type}</Badge>
                      {attr.required ? <Badge variant="secondary">required</Badge> : null}
                    </div>
                    <p className="font-mono text-xs text-muted-foreground">{attr.slug}</p>
                    {attr.attribute_type === "picklist" && attr.picklist_options.length > 0 ? (
                      <p className="mt-1 text-xs text-muted-foreground">
                        Options: {attr.picklist_options.join(", ")}
                      </p>
                    ) : null}
                  </div>
                  <div className="flex gap-1">
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => startEdit(attr)}
                      aria-label={`Edit ${attr.name}`}
                    >
                      <Pencil className="h-4 w-4" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => void handleDelete(attr.id)}
                      aria-label={`Delete ${attr.name}`}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">No attributes yet.</p>
        )}
      </CardContent>
    </Card>
  );
}
