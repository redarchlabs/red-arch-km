"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { use, useCallback, useEffect, useState } from "react";

import { FormRenderer } from "@/components/forms/FormRenderer";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { getEntity, type EntityDefinition } from "@/lib/api/entities";
import { listRecords, type EntityRecord } from "@/lib/api/entityRecords";
import { getApiErrorMessage } from "@/lib/api/errors";
import { getForm, getFormRender, submitForm, type FormRender, type FormSubmit } from "@/lib/api/forms";
import { runWorkflow } from "@/lib/api/workflows";

function recordLabel(rec: EntityRecord): string {
  for (const key of ["name", "title", "email", "first_name", "last_name"]) {
    if (typeof rec[key] === "string" && rec[key]) return rec[key] as string;
  }
  return rec.id.slice(0, 8);
}

/**
 * Authenticated internal data-entry surface: staff pick a record and fill the
 * form through the shared `<FormRenderer>` (no token). Submits on the tenant
 * session via `POST /forms/{id}/submit`; run-workflow buttons fire the manual-run
 * endpoint against the chosen record.
 */
export default function FormFillPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);

  const [entity, setEntity] = useState<EntityDefinition | null>(null);
  const [records, setRecords] = useState<EntityRecord[]>([]);
  const [recordId, setRecordId] = useState("");
  const [render, setRender] = useState<FormRender | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const boot = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const form = await getForm(id);
      const e = await getEntity(form.entity_definition_id);
      setEntity(e);
      const recs = (await listRecords(e.slug, { limit: 100 }).catch(() => ({ items: [] as EntityRecord[] }))).items;
      setRecords(recs);
      if (recs.length > 0) setRecordId(recs[0].id);
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to load form"));
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    void boot();
  }, [boot]);

  useEffect(() => {
    if (!recordId) {
      setRender(null);
      return;
    }
    let active = true;
    getFormRender(id, recordId)
      .then((r) => active && setRender(r))
      .catch((e: unknown) => active && setError(getApiErrorMessage(e, "Failed to load form")));
    return () => {
      active = false;
    };
  }, [id, recordId]);

  const handleSubmit = async (payload: FormSubmit) => {
    if (!recordId) return;
    setSubmitting(true);
    setSubmitError(null);
    setNotice(null);
    try {
      await submitForm(id, recordId, payload);
      setNotice("Saved.");
    } catch (e: unknown) {
      setSubmitError(getApiErrorMessage(e, "Save failed"));
    } finally {
      setSubmitting(false);
    }
  };

  const handleRunWorkflow = async (workflowId: string, inputs: Record<string, unknown>) => {
    setNotice(null);
    setSubmitError(null);
    try {
      await runWorkflow(workflowId, { operation: "update", record_id: recordId || null, after: inputs });
      setNotice("Workflow started.");
    } catch (e: unknown) {
      setSubmitError(getApiErrorMessage(e, "Workflow failed to start"));
    }
  };

  if (loading) return <Skeleton className="h-96 w-full" />;
  if (!entity) return <p className="text-sm text-destructive">{error ?? "Form not found."}</p>;

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Link href={`/forms/${id}`} className="text-muted-foreground hover:text-foreground">
          <ArrowLeft className="h-5 w-5" />
        </Link>
        <div className="flex-1">
          <h1 className="text-2xl font-semibold">{render?.form_name ?? "Fill form"}</h1>
          <p className="text-sm text-muted-foreground">Enter data into a {entity.name} record</p>
        </div>
      </div>

      {error ? <p className="text-sm text-destructive">{error}</p> : null}

      <Card>
        <CardContent className="space-y-4 pt-6">
          <div className="space-y-1">
            <label className="block text-sm font-medium">Record</label>
            {records.length === 0 ? (
              <p className="text-sm text-muted-foreground">No {entity.name} records yet.</p>
            ) : (
              <select
                value={recordId}
                onChange={(e) => setRecordId(e.target.value)}
                className="h-9 w-full max-w-sm rounded-md border bg-background px-2 text-sm"
              >
                {records.map((r) => (
                  <option key={r.id} value={r.id}>
                    {recordLabel(r)}
                  </option>
                ))}
              </select>
            )}
          </div>

          {notice ? <p className="text-sm text-green-600">{notice}</p> : null}

          {render ? (
            <FormRenderer
              render={render}
              mode="fill"
              onSubmit={handleSubmit}
              onRunWorkflow={handleRunWorkflow}
              submitting={submitting}
              error={submitError}
              defaultSubmitLabel="Save"
            />
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}
