"use client";

import { Pencil, Plus, Search, Trash2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { DynamicForm, type RelationshipFormField } from "@/components/entities/DynamicForm";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  listEntities,
  listRelationships,
  type EntityDefinition,
  type EntityField,
} from "@/lib/api/entities";
import {
  createRecord,
  deleteRecord,
  listRecords,
  updateRecord,
  type EntityRecord,
} from "@/lib/api/entityRecords";
import { getApiErrorMessage } from "@/lib/api/errors";

interface EntityRecordsTableProps {
  entity: EntityDefinition;
}

/** Show at most this many field columns to keep the grid readable. */
const MAX_COLUMNS = 6;

/** Rows fetched per keyset page. */
const PAGE_SIZE = 50;

export function EntityRecordsTable({ entity }: EntityRecordsTableProps) {
  const [records, setRecords] = useState<EntityRecord[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [editing, setEditing] = useState<EntityRecord | "new" | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [relFields, setRelFields] = useState<RelationshipFormField[]>([]);
  const [relLoading, setRelLoading] = useState(false);

  const columns = entity.fields.slice(0, MAX_COLUMNS);

  // Search only helps when the entity has text-like columns to match against.
  const hasSearchable = useMemo(
    () =>
      entity.fields.some((f) =>
        (["text", "long_text", "picklist"] as const).includes(
          f.field_type as "text" | "long_text" | "picklist",
        ),
      ),
    [entity.fields],
  );

  // Debounce the search box so we don't fire a query per keystroke.
  useEffect(() => {
    const handle = setTimeout(() => setDebouncedSearch(search.trim()), 300);
    return () => clearTimeout(handle);
  }, [search]);

  // Load to-one relationships and their target-record options for the picker.
  // Refreshed each time the form opens so newly-created target records appear.
  const loadRelationships = useCallback(async () => {
    setRelLoading(true);
    try {
      const [rels, allEntities] = await Promise.all([
        listRelationships(entity.id),
        listEntities(),
      ]);
      const byId = new Map(allEntities.map((e) => [e.id, e]));
      const toOne = rels.filter((r) => r.cardinality !== "many_to_many");
      const built = await Promise.all(
        toOne.map(async (r): Promise<RelationshipFormField> => {
          const target = byId.get(r.target_definition_id);
          const options = target
            ? (await listRecords(target.slug, { limit: 200 })).items.map((rec) => ({
                value: String(rec.id),
                label: recordLabel(rec, target.fields),
              }))
            : [];
          return {
            id: r.id,
            slug: r.slug,
            name: r.name,
            is_required: r.is_required,
            targetEntityName: target?.name ?? "record",
            options,
          };
        }),
      );
      setRelFields(built);
    } catch {
      // Relationship pickers are best-effort; scalar fields still work without them.
      setRelFields([]);
    } finally {
      setRelLoading(false);
    }
  }, [entity.id]);

  const openForm = useCallback(
    (target: EntityRecord | "new") => {
      setFormError(null);
      setEditing(target);
      void loadRelationships();
    },
    [loadRelationships],
  );

  // Fetch the first keyset page for the current search term.
  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const result = await listRecords(entity.slug, {
        search: debouncedSearch,
        limit: PAGE_SIZE,
      });
      setRecords(result.items);
      setNextCursor(result.next_cursor);
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to load records"));
    } finally {
      setIsLoading(false);
    }
  }, [entity.slug, debouncedSearch]);

  // Append the next keyset page (cursor-based; no OFFSET).
  const loadMore = useCallback(async () => {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const result = await listRecords(entity.slug, {
        search: debouncedSearch,
        cursor: nextCursor,
        limit: PAGE_SIZE,
      });
      setRecords((prev) => [...prev, ...result.items]);
      setNextCursor(result.next_cursor);
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to load more records"));
    } finally {
      setLoadingMore(false);
    }
  }, [entity.slug, debouncedSearch, nextCursor, loadingMore]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleSubmit = async (data: Record<string, unknown>) => {
    setBusy(true);
    setFormError(null);
    try {
      if (editing === "new") {
        await createRecord(entity.slug, data);
      } else if (editing) {
        await updateRecord(entity.slug, String(editing.id), data);
      }
      setEditing(null);
      await load();
    } catch (e: unknown) {
      setFormError(getApiErrorMessage(e, "Save failed"));
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async (record: EntityRecord) => {
    if (!confirm("Delete this record?")) return;
    try {
      await deleteRecord(entity.slug, String(record.id));
      await load();
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Delete failed"));
    }
  };

  return (
    <Card>
      <CardContent className="space-y-4 pt-6">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold">Records</h2>
            <p className="text-sm text-muted-foreground">
              {isLoading
                ? "Loading…"
                : `Showing ${records.length}${nextCursor ? "+" : ""}` +
                  (debouncedSearch ? ` matching “${debouncedSearch}”` : "")}
            </p>
          </div>
          <Button
            onClick={() => openForm("new")}
            disabled={entity.fields.length === 0}
          >
            <Plus className="h-4 w-4" />
            New record
          </Button>
        </div>

        {hasSearchable ? (
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={`Search ${entity.name}…`}
              className="pl-9"
              aria-label={`Search ${entity.name} records`}
            />
          </div>
        ) : null}

        {error ? <p className="text-sm text-destructive">{error}</p> : null}

        {isLoading ? (
          <Skeleton className="h-24 w-full" />
        ) : records.length > 0 ? (
          <div className="overflow-x-auto rounded-md border">
            <table className="w-full text-sm">
              <thead className="border-b bg-muted/50">
                <tr>
                  {columns.map((f) => (
                    <th key={f.id} className="px-3 py-2 text-left font-medium">
                      {f.name}
                    </th>
                  ))}
                  <th className="w-20 px-3 py-2" />
                </tr>
              </thead>
              <tbody>
                {records.map((record) => (
                  <tr key={String(record.id)} className="border-b last:border-0">
                    {columns.map((f) => (
                      <td key={f.id} className="px-3 py-2">
                        {formatCell(record[f.slug])}
                      </td>
                    ))}
                    <td className="px-3 py-2">
                      <div className="flex justify-end gap-1">
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => openForm(record)}
                          aria-label="Edit record"
                        >
                          <Pencil className="h-4 w-4" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => void handleDelete(record)}
                          aria-label="Delete record"
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            {entity.fields.length === 0
              ? "Add a field before creating records."
              : debouncedSearch
                ? `No records match “${debouncedSearch}”.`
                : "No records yet."}
          </p>
        )}

        {!isLoading && nextCursor ? (
          <div className="flex justify-center pt-1">
            <Button variant="outline" onClick={() => void loadMore()} disabled={loadingMore}>
              {loadingMore ? "Loading…" : "Load more"}
            </Button>
          </div>
        ) : null}
      </CardContent>

      <Dialog open={editing !== null} onClose={() => setEditing(null)}>
        <DialogHeader>
          <DialogTitle>{editing === "new" ? "New" : "Edit"} {entity.name}</DialogTitle>
        </DialogHeader>
        {editing !== null ? (
          <DynamicForm
            fields={entity.fields}
            relationships={relFields}
            relationshipsLoading={relLoading}
            initial={editing === "new" ? undefined : editing}
            submitLabel={editing === "new" ? "Create" : "Save"}
            busy={busy}
            error={formError}
            onSubmit={handleSubmit}
            onCancel={() => setEditing(null)}
          />
        ) : null}
      </Dialog>
    </Card>
  );
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/** Human-readable label for a target record: first non-empty field, else a short id. */
function recordLabel(record: EntityRecord, fields: EntityField[]): string {
  for (const f of fields) {
    const v = record[f.slug];
    if (v !== null && v !== undefined && String(v).trim() !== "") {
      return String(v);
    }
  }
  return `#${String(record.id).slice(0, 8)}`;
}
