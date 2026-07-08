"use client";

import { ArrowLeft, Copy, Eye, LinkIcon } from "lucide-react";
import Link from "next/link";
import { use, useCallback, useEffect, useMemo, useState } from "react";

import { FormPreview } from "@/components/forms/FormPreview";
import { LayoutBuilder, type BuilderCtx } from "@/components/forms/builder/LayoutBuilder";
import { useHelpOverride } from "@/context/HelpContext";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  getEntity,
  listEntities,
  listRelationships,
  type EntityDefinition,
  type EntityRelationship,
} from "@/lib/api/entities";
import { listRecords, type EntityRecord } from "@/lib/api/entityRecords";
import { getApiErrorMessage } from "@/lib/api/errors";
import {
  generateFormLink,
  getForm,
  listFormLinks,
  revokeFormLink,
  updateForm,
  type Form,
  type FormElement,
  type FormLink,
} from "@/lib/api/forms";
import { buildRenderFromConfig } from "@/lib/forms/catalogFromEntities";

function recordLabel(rec: EntityRecord): string {
  for (const key of ["name", "title", "email", "first_name", "last_name"]) {
    if (typeof rec[key] === "string" && rec[key]) return rec[key] as string;
  }
  return rec.id.slice(0, 8);
}

export default function FormBuilderPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);

  // The builder pushes per-element help on focus; clear it when leaving the page.
  const clearHelp = useHelpOverride();
  useEffect(() => () => clearHelp(null), [clearHelp]);

  const [form, setForm] = useState<Form | null>(null);
  const [entity, setEntity] = useState<EntityDefinition | null>(null);
  const [elements, setElements] = useState<FormElement[]>([]);
  const [allEntities, setAllEntities] = useState<EntityDefinition[]>([]);
  const [allRels, setAllRels] = useState<EntityRelationship[]>([]);
  const [links, setLinks] = useState<FormLink[]>([]);
  const [records, setRecords] = useState<EntityRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [showPreview, setShowPreview] = useState(false);

  const [targetRecordId, setTargetRecordId] = useState("");
  const [email, setEmail] = useState("");
  const [generating, setGenerating] = useState(false);
  const [newUrl, setNewUrl] = useState<string | null>(null);
  const [emailSent, setEmailSent] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const f = await getForm(id);
      const e = await getEntity(f.entity_definition_id);
      setForm(f);
      setEntity(e);
      setElements(f.config.elements ?? []);
      const [ls, recs, all] = await Promise.all([
        listFormLinks(id),
        listRecords(e.slug, { limit: 100 }).then((r) => r.items).catch(() => []),
        listEntities().catch(() => []),
      ]);
      setLinks(ls);
      setRecords(recs);
      setAllEntities(all);
      // Load every entity's relationships so nested section/table pickers resolve.
      const rels = await Promise.all(all.map((ent) => listRelationships(ent.id).catch(() => [])));
      const byId = new Map<string, EntityRelationship>();
      for (const r of rels.flat()) byId.set(r.id, r);
      setAllRels([...byId.values()]);
      if (recs.length > 0) setTargetRecordId(recs[0].id);
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to load form"));
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
      const updated = await updateForm(id, { config: { version: 2, elements } });
      setForm(updated);
      setSaved(true);
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to save form"));
    } finally {
      setSaving(false);
    }
  };

  const handleRevoke = async (linkId: string) => {
    if (!confirm("Revoke this link? The token will stop working immediately.")) return;
    try {
      await revokeFormLink(id, linkId);
      setLinks(await listFormLinks(id));
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to revoke link"));
    }
  };

  const handleGenerate = async () => {
    if (!targetRecordId) return;
    setGenerating(true);
    setNewUrl(null);
    setError(null);
    try {
      const created = await generateFormLink(id, {
        target_record_id: targetRecordId,
        recipient_email: email.trim() || null,
      });
      setNewUrl(created.url);
      setEmailSent(created.email_sent);
      setLinks(await listFormLinks(id));
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to generate link"));
    } finally {
      setGenerating(false);
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
    };
  }, [allEntities, allRels]);

  const previewRender = useMemo(
    () =>
      form && entity
        ? buildRenderFromConfig(form.name, entity.id, { version: 2, elements }, allEntities, allRels)
        : null,
    [form, entity, elements, allEntities, allRels],
  );

  if (loading) return <Skeleton className="h-96 w-full" />;
  if (!form || !entity) return <p className="text-sm text-destructive">{error ?? "Form not found."}</p>;

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Link href="/forms" className="text-muted-foreground hover:text-foreground">
          <ArrowLeft className="h-5 w-5" />
        </Link>
        <div className="flex-1">
          <h1 className="text-2xl font-semibold">{form.name}</h1>
          <p className="text-sm text-muted-foreground">
            Collects into <span className="font-medium">{entity.name}</span>
          </p>
        </div>
        <Button type="button" variant="outline" onClick={() => setShowPreview(true)}>
          <Eye className="h-4 w-4" />
          Preview
        </Button>
        <Link href={`/forms/${id}/fill`}>
          <Button type="button" variant="outline">
            Fill
          </Button>
        </Link>
        <Button onClick={() => void handleSave()} disabled={saving} size="sm">
          {saving ? "Saving…" : saved ? "Saved" : "Save"}
        </Button>
      </div>

      {error ? <p className="text-sm text-destructive">{error}</p> : null}

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardContent className="space-y-4 pt-6">
            <h2 className="text-lg font-semibold">Layout</h2>
            <p className="text-sm text-muted-foreground">
              Compose the form: add fields, labels, calculated values, buttons, tables, and layout
              containers (tabs, panels, accordions, columns). Nest freely.
            </p>
            <LayoutBuilder elements={elements} entityId={entity.id} ctx={ctx} onChange={setElements} />
          </CardContent>
        </Card>

        <Card>
          <CardContent className="space-y-4 pt-6">
            <h2 className="text-lg font-semibold">Send a link</h2>
            <p className="text-sm text-muted-foreground">
              Generate a single-use link bound to one {entity.name} record.
            </p>
            {records.length === 0 ? (
              <p className="text-sm text-muted-foreground">No {entity.name} records yet — create one first.</p>
            ) : (
              <div className="space-y-2">
                <label className="block text-sm font-medium">Record to update</label>
                <select
                  value={targetRecordId}
                  onChange={(e) => setTargetRecordId(e.target.value)}
                  className="h-9 w-full rounded-md border bg-background px-2 text-sm"
                >
                  {records.map((r) => (
                    <option key={r.id} value={r.id}>
                      {recordLabel(r)}
                    </option>
                  ))}
                </select>
                <Input
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="Recipient email (optional)"
                />
                <Button onClick={() => void handleGenerate()} disabled={generating || elements.length === 0}>
                  <LinkIcon className="h-4 w-4" />
                  {generating ? "Generating…" : "Generate link"}
                </Button>
              </div>
            )}

            {newUrl ? (
              <div className="rounded-md border bg-muted/40 p-3">
                <p className="mb-1 text-xs font-medium text-muted-foreground">
                  {emailSent ? "Emailed to the recipient. " : ""}
                  Shareable link (copy it now — the token isn&apos;t shown again):
                </p>
                <div className="flex items-center gap-2">
                  <code className="flex-1 truncate rounded bg-background px-2 py-1 text-xs">{newUrl}</code>
                  <Button
                    type="button"
                    variant="outline"
                    size="icon"
                    className="h-7 w-7"
                    onClick={() => void navigator.clipboard?.writeText(newUrl)}
                    aria-label="Copy link"
                  >
                    <Copy className="h-4 w-4" />
                  </Button>
                </div>
              </div>
            ) : null}

            {links.length > 0 ? (
              <div className="space-y-1">
                <h3 className="text-sm font-medium">Links</h3>
                <ul className="divide-y rounded-md border text-sm">
                  {links.map((link) => (
                    <li key={link.id} className="flex items-center justify-between gap-2 px-3 py-2">
                      <span className="flex-1 truncate text-muted-foreground">{link.recipient_email || "—"}</span>
                      <span className="text-xs">{link.status}</span>
                      {link.status === "pending" ? (
                        <button
                          type="button"
                          onClick={() => void handleRevoke(link.id)}
                          className="text-xs text-muted-foreground hover:text-destructive"
                        >
                          Revoke
                        </button>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </CardContent>
        </Card>
      </div>

      <Dialog open={showPreview} onClose={() => setShowPreview(false)} className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{form.name}</DialogTitle>
          <DialogDescription>
            {saved ? "Preview of the saved form." : "Preview reflects your current edits — save to publish."}
          </DialogDescription>
        </DialogHeader>
        <div className="max-h-[70vh] overflow-y-auto pr-1">
          {previewRender ? <FormPreview render={previewRender} /> : null}
        </div>
      </Dialog>
    </div>
  );
}
