"use client";

import { ArrowLeft, Eye } from "lucide-react";
import Link from "next/link";
import { use, useCallback, useEffect, useMemo, useState } from "react";

import { FormPreview } from "@/components/forms/FormPreview";
import { LayoutBuilder, type BuilderCtx } from "@/components/forms/builder/LayoutBuilder";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import {
  listEntities,
  listRelationships,
  type EntityDefinition,
  type EntityRelationship,
} from "@/lib/api/entities";
import { getApiErrorMessage } from "@/lib/api/errors";
import { listForms, type FormElement } from "@/lib/api/forms";
import { getView, updateView, type View } from "@/lib/api/views";
import { buildRenderFromConfig } from "@/lib/forms/catalogFromEntities";

export default function ViewBuilderPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);

  const [view, setView] = useState<View | null>(null);
  const [elements, setElements] = useState<FormElement[]>([]);
  const [allEntities, setAllEntities] = useState<EntityDefinition[]>([]);
  const [allRels, setAllRels] = useState<EntityRelationship[]>([]);
  const [forms, setForms] = useState<{ id: string; name: string }[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [showPreview, setShowPreview] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const v = await getView(id);
      setView(v);
      setElements(v.config.elements ?? []);
      const [all, fs] = await Promise.all([
        listEntities().catch(() => []),
        listForms().catch(() => []),
      ]);
      setAllEntities(all);
      setForms(fs.map((f) => ({ id: f.id, name: f.name })));
      const rels = await Promise.all(all.map((e) => listRelationships(e.id).catch(() => [])));
      const byId = new Map<string, EntityRelationship>();
      for (const r of rels.flat()) byId.set(r.id, r);
      setAllRels([...byId.values()]);
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to load view"));
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleSave = async () => {
    setSaving(true);
    setSaved(false);
    setError(null);
    try {
      const updated = await updateView(id, { config: { version: 2, elements } });
      setView(updated);
      setSaved(true);
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to save view"));
    } finally {
      setSaving(false);
    }
  };

  const ctx = useMemo<BuilderCtx>(() => {
    const entitiesById = new Map(allEntities.map((e) => [e.id, e]));
    return {
      entitiesById,
      toOneOutgoing: (eid) =>
        allRels.filter(
          (r) =>
            r.source_definition_id === eid &&
            (r.cardinality === "many_to_one" || r.cardinality === "one_to_one"),
        ),
      incomingToMany: (eid) =>
        allRels.filter((r) => r.target_definition_id === eid && r.cardinality === "many_to_one"),
      forms,
    };
  }, [allEntities, allRels, forms]);

  const previewRender = useMemo(
    () =>
      view
        ? buildRenderFromConfig(
            view.name,
            view.entity_definition_id ?? "",
            { version: 2, elements },
            allEntities,
            allRels,
          )
        : null,
    [view, elements, allEntities, allRels],
  );

  if (loading) return <Skeleton className="h-96 w-full" />;
  if (!view) return <p className="text-sm text-destructive">{error ?? "View not found."}</p>;

  const entityBound = !!view.entity_definition_id;

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Link href="/views" className="text-muted-foreground hover:text-foreground">
          <ArrowLeft className="h-5 w-5" />
        </Link>
        <div className="flex-1">
          <h1 className="text-2xl font-semibold">{view.name}</h1>
          <p className="text-sm text-muted-foreground">
            {entityBound ? "Entity-bound view" : "Standalone view — forms, buttons & layout"}
          </p>
        </div>
        <Button type="button" variant="outline" onClick={() => setShowPreview(true)}>
          <Eye className="h-4 w-4" />
          Preview
        </Button>
        <Link href={`/views/${id}/view`}>
          <Button type="button" variant="outline">
            Open
          </Button>
        </Link>
        <Button onClick={() => void handleSave()} disabled={saving} size="sm">
          {saving ? "Saving…" : saved ? "Saved" : "Save"}
        </Button>
      </div>

      {error ? <p className="text-sm text-destructive">{error}</p> : null}

      <Card>
        <CardContent className="space-y-4 pt-6">
          <h2 className="text-lg font-semibold">Layout</h2>
          <LayoutBuilder
            elements={elements}
            entityId={view.entity_definition_id ?? ""}
            ctx={ctx}
            allow={entityBound ? "all" : "view"}
            onChange={setElements}
          />
        </CardContent>
      </Card>

      <Dialog open={showPreview} onClose={() => setShowPreview(false)} className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{view.name}</DialogTitle>
        </DialogHeader>
        <div className="max-h-[70vh] overflow-y-auto pr-1">
          {previewRender ? <FormPreview render={previewRender} /> : null}
        </div>
      </Dialog>
    </div>
  );
}
