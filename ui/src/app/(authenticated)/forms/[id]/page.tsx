"use client";

import { ArrowLeft, Copy, LinkIcon } from "lucide-react";
import Link from "next/link";
import { use, useCallback, useEffect, useState } from "react";

import { FormFieldsEditor } from "@/components/forms/FormFieldsEditor";
import { FormSectionsEditor } from "@/components/forms/FormSectionsEditor";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  getEntity,
  listEntities,
  listIncomingRelationships,
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
  updateForm,
  type Form,
  type FormFieldConfig,
  type FormLink,
  type FormSectionConfig,
} from "@/lib/api/forms";

function recordLabel(rec: EntityRecord): string {
  // Prefer a human-ish column; fall back to the id.
  for (const key of ["name", "title", "email", "first_name", "last_name"]) {
    if (typeof rec[key] === "string" && rec[key]) return rec[key] as string;
  }
  return rec.id.slice(0, 8);
}

export default function FormBuilderPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);

  const [form, setForm] = useState<Form | null>(null);
  const [entity, setEntity] = useState<EntityDefinition | null>(null);
  const [fields, setFields] = useState<FormFieldConfig[]>([]);
  const [sections, setSections] = useState<FormSectionConfig[]>([]);
  const [allEntities, setAllEntities] = useState<EntityDefinition[]>([]);
  const [outgoing, setOutgoing] = useState<EntityRelationship[]>([]);
  const [incoming, setIncoming] = useState<EntityRelationship[]>([]);
  const [links, setLinks] = useState<FormLink[]>([]);
  const [records, setRecords] = useState<EntityRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  // Link generation.
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
      setFields(f.config.fields ?? []);
      setSections(f.config.sections ?? []);
      const [ls, recs, all, out, inc] = await Promise.all([
        listFormLinks(id),
        listRecords(e.slug, { limit: 100 }).then((r) => r.items).catch(() => []),
        listEntities().catch(() => []),
        listRelationships(f.entity_definition_id).catch(() => []),
        listIncomingRelationships(f.entity_definition_id).catch(() => []),
      ]);
      setLinks(ls);
      setRecords(recs);
      setAllEntities(all);
      setOutgoing(out);
      setIncoming(inc);
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
    if (!entity) return;
    setSaving(true);
    setSaved(false);
    setError(null);
    try {
      const updated = await updateForm(id, { config: { fields, sections } });
      setForm(updated);
      setSaved(true);
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to save form"));
    } finally {
      setSaving(false);
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

  if (loading) return <Skeleton className="h-96 w-full" />;
  if (!form || !entity) return <p className="text-sm text-destructive">{error ?? "Form not found."}</p>;

  const includedCount = fields.length;

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Link href="/forms" className="text-muted-foreground hover:text-foreground">
          <ArrowLeft className="h-5 w-5" />
        </Link>
        <div>
          <h1 className="text-2xl font-semibold">{form.name}</h1>
          <p className="text-sm text-muted-foreground">
            Collects into <span className="font-medium">{entity.name}</span> · updates the emailed record
          </p>
        </div>
      </div>

      {error ? <p className="text-sm text-destructive">{error}</p> : null}

      <div className="grid gap-4 lg:grid-cols-2">
        {/* Field selection */}
        <Card>
          <CardContent className="space-y-4 pt-6">
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-semibold">Fields on this form</h2>
              <Button onClick={() => void handleSave()} disabled={saving} size="sm">
                {saving ? "Saving…" : saved ? "Saved" : "Save"}
              </Button>
            </div>
            <p className="text-sm text-muted-foreground">
              Add fields, drag their order, and set label, width, placeholder, and
              group headings. {includedCount} on the form.
            </p>
            <FormFieldsEditor
              entityFields={entity.fields}
              fields={fields}
              onChange={setFields}
            />
          </CardContent>
        </Card>

        {/* Related sections (1:1 + 1:M) */}
        <Card>
          <CardContent className="space-y-4 pt-6">
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-semibold">Related records</h2>
              <Button onClick={() => void handleSave()} disabled={saving} size="sm" variant="outline">
                {saving ? "Saving…" : "Save"}
              </Button>
            </div>
            <FormSectionsEditor
              allEntities={allEntities}
              outgoing={outgoing}
              incoming={incoming}
              sections={sections}
              onChange={setSections}
            />
          </CardContent>
        </Card>

        {/* Link generation */}
        <Card>
          <CardContent className="space-y-4 pt-6">
            <h2 className="text-lg font-semibold">Send a link</h2>
            <p className="text-sm text-muted-foreground">
              Generate a single-use link bound to one {entity.name} record. The recipient updates that record.
            </p>
            {records.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No {entity.name} records yet — create one first.
              </p>
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
                  placeholder="Recipient email (optional, for your records)"
                />
                <Button onClick={() => void handleGenerate()} disabled={generating || includedCount === 0}>
                  <LinkIcon className="h-4 w-4" />
                  {generating ? "Generating…" : "Generate link"}
                </Button>
                {includedCount === 0 ? (
                  <p className="text-xs text-muted-foreground">Select at least one field and save first.</p>
                ) : null}
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
                    <li key={link.id} className="flex items-center justify-between px-3 py-2">
                      <span className="text-muted-foreground">{link.recipient_email || "—"}</span>
                      <span className="text-xs">{link.status}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
