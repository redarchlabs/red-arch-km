"use client";

import { Plus, Trash2, X } from "lucide-react";
import { use, useCallback, useEffect, useState } from "react";

import {
  getPublicForm,
  submitPublicForm,
  type PublicForm,
  type PublicFormField,
  type PublicFormSection,
} from "@/lib/api/forms";

/**
 * Public, unauthenticated intake page. The external user arrives with only a
 * token in the URL; the API resolves the org from it. No app chrome, no auth.
 * Renders root fields plus related sections: 1:1 inline or in a modal, and 1:M
 * as an add/remove-row table.
 */
export default function IntakeFormPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = use(params);

  const [form, setForm] = useState<PublicForm | null>(null);
  const [values, setValues] = useState<Record<string, unknown>>({});
  // 1:1 (inline/modal) section → its single record's values, keyed by section.key.
  const [sectionValues, setSectionValues] = useState<Record<string, Record<string, unknown>>>({});
  // 1:M (table) section → its rows, keyed by section.key.
  const [sectionRows, setSectionRows] = useState<Record<string, Record<string, unknown>[]>>({});
  const [openModal, setOpenModal] = useState<string | null>(null);

  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const data = await getPublicForm(token);
      setForm(data);
      setValues({ ...data.values });
      const sv: Record<string, Record<string, unknown>> = {};
      const sr: Record<string, Record<string, unknown>[]> = {};
      for (const s of data.sections) {
        if (s.mode === "table") sr[s.key] = s.rows.length ? [...s.rows] : [{}];
        else sv[s.key] = { ...s.values };
      }
      setSectionValues(sv);
      setSectionRows(sr);
    } catch (e: unknown) {
      setLoadError(e instanceof Error ? e.message : "Unable to load this form.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    void load();
  }, [load]);

  const setField = (slug: string, value: unknown) =>
    setValues((prev) => ({ ...prev, [slug]: value }));

  const setSectionField = (key: string, slug: string, value: unknown) =>
    setSectionValues((prev) => ({ ...prev, [key]: { ...prev[key], [slug]: value } }));

  const setRowField = (key: string, idx: number, slug: string, value: unknown) =>
    setSectionRows((prev) => {
      const rows = [...(prev[key] ?? [])];
      rows[idx] = { ...rows[idx], [slug]: value };
      return { ...prev, [key]: rows };
    });

  const addRow = (key: string) =>
    setSectionRows((prev) => ({ ...prev, [key]: [...(prev[key] ?? []), {}] }));

  const removeRow = (key: string, idx: number) =>
    setSectionRows((prev) => ({ ...prev, [key]: (prev[key] ?? []).filter((_, i) => i !== idx) }));

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const sections: Record<string, unknown> = {};
      for (const s of form.sections) {
        if (s.mode === "table") {
          // Drop fully-empty rows so an untouched blank row isn't submitted.
          const rows = (sectionRows[s.key] ?? []).filter((r) =>
            Object.values(r).some((v) => v !== "" && v != null),
          );
          if (rows.length) sections[s.key] = { rows };
        } else {
          const v = sectionValues[s.key] ?? {};
          if (Object.values(v).some((x) => x !== "" && x != null)) sections[s.key] = { values: v };
        }
      }
      await submitPublicForm(token, { values, sections });
      setDone(true);
    } catch (e: unknown) {
      setSubmitError(e instanceof Error ? e.message : "Submission failed.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="mx-auto flex min-h-screen max-w-xl flex-col justify-center px-4 py-10">
      {loading ? (
        <p className="text-center text-muted-foreground">Loading…</p>
      ) : loadError ? (
        <Notice title="This form isn't available" body={loadError} />
      ) : done ? (
        <Notice title="Thank you" body="Your response has been submitted." tone="success" />
      ) : form && form.status !== "pending" ? (
        <Notice
          title={form.status === "submitted" ? "Already submitted" : "Link expired"}
          body={
            form.status === "submitted"
              ? "This form has already been completed."
              : "This form link is no longer active. Please request a new one."
          }
        />
      ) : form ? (
        <form onSubmit={handleSubmit} className="space-y-6 rounded-lg border bg-card p-6 shadow-sm">
          <header className="space-y-1">
            <h1 className="text-2xl font-semibold">{form.form_name}</h1>
            {form.description ? (
              <p className="text-sm text-muted-foreground">{form.description}</p>
            ) : null}
          </header>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            {form.fields.map((field) => (
              <div key={field.slug} className="contents">
                {field.heading ? (
                  <h2 className="border-b pb-1 text-base font-semibold sm:col-span-2">
                    {field.heading}
                  </h2>
                ) : null}
                <div className={field.width === "half" ? "sm:col-span-1" : "sm:col-span-2"}>
                  <Field
                    field={field}
                    value={values[field.slug]}
                    onChange={(v) => setField(field.slug, v)}
                  />
                </div>
              </div>
            ))}
          </div>

          {form.sections.map((section) => (
            <Section
              key={section.key}
              section={section}
              inlineValues={sectionValues[section.key] ?? {}}
              rows={sectionRows[section.key] ?? []}
              onInlineChange={(slug, v) => setSectionField(section.key, slug, v)}
              onRowChange={(idx, slug, v) => setRowField(section.key, idx, slug, v)}
              onAddRow={() => addRow(section.key)}
              onRemoveRow={(idx) => removeRow(section.key, idx)}
              modalOpen={openModal === section.key}
              onOpenModal={() => setOpenModal(section.key)}
              onCloseModal={() => setOpenModal(null)}
            />
          ))}

          {submitError ? <p className="text-sm text-destructive">{submitError}</p> : null}

          <button
            type="submit"
            disabled={submitting}
            className="w-full rounded-md bg-primary px-4 py-2.5 font-medium text-primary-foreground disabled:opacity-60"
          >
            {submitting ? "Submitting…" : "Submit"}
          </button>
        </form>
      ) : null}
    </main>
  );
}

function Notice({
  title,
  body,
  tone = "neutral",
}: {
  title: string;
  body: string;
  tone?: "neutral" | "success";
}) {
  return (
    <div className="rounded-lg border bg-card p-8 text-center shadow-sm">
      <h1 className={`text-xl font-semibold ${tone === "success" ? "text-green-600" : ""}`}>{title}</h1>
      <p className="mt-2 text-sm text-muted-foreground">{body}</p>
    </div>
  );
}

const inputClass =
  "w-full rounded-md border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring";

interface SectionProps {
  section: PublicFormSection;
  inlineValues: Record<string, unknown>;
  rows: Record<string, unknown>[];
  onInlineChange: (slug: string, v: unknown) => void;
  onRowChange: (idx: number, slug: string, v: unknown) => void;
  onAddRow: () => void;
  onRemoveRow: (idx: number) => void;
  modalOpen: boolean;
  onOpenModal: () => void;
  onCloseModal: () => void;
}

function Section(props: SectionProps) {
  const { section } = props;
  const heading = <h2 className="text-lg font-semibold">{section.label}</h2>;

  if (section.mode === "table") {
    return (
      <div className="space-y-2 border-t pt-4">
        {heading}
        <div className="space-y-3">
          {props.rows.map((row, idx) => (
            <div key={idx} className="relative rounded-md border p-3">
              {props.rows.length > 1 ? (
                <button
                  type="button"
                  onClick={() => props.onRemoveRow(idx)}
                  className="absolute right-2 top-2 text-muted-foreground hover:text-destructive"
                  aria-label="Remove row"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              ) : null}
              <div className="space-y-3">
                {section.fields.map((f) => (
                  <Field
                    key={f.slug}
                    field={f}
                    name={`${section.key}-${idx}-${f.slug}`}
                    value={row[f.slug]}
                    onChange={(v) => props.onRowChange(idx, f.slug, v)}
                  />
                ))}
              </div>
            </div>
          ))}
        </div>
        <button
          type="button"
          onClick={props.onAddRow}
          className="inline-flex items-center gap-1 rounded-md border px-3 py-1.5 text-sm hover:bg-muted"
        >
          <Plus className="h-4 w-4" /> Add {section.entity_name}
        </button>
      </div>
    );
  }

  // 1:1 modal.
  if (section.mode === "modal") {
    const filled = Object.values(props.inlineValues).some((v) => v !== "" && v != null);
    return (
      <div className="space-y-2 border-t pt-4">
        {heading}
        <button
          type="button"
          onClick={props.onOpenModal}
          className="inline-flex items-center gap-1 rounded-md border px-3 py-1.5 text-sm hover:bg-muted"
        >
          <Plus className="h-4 w-4" /> {filled ? `Edit ${section.entity_name}` : `Add ${section.entity_name}`}
        </button>
        {props.modalOpen ? (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
            <div className="w-full max-w-md space-y-4 rounded-lg border bg-card p-6 shadow-lg">
              <div className="flex items-center justify-between">
                <h3 className="text-lg font-semibold">{section.label}</h3>
                <button type="button" onClick={props.onCloseModal} aria-label="Close">
                  <X className="h-5 w-5" />
                </button>
              </div>
              <div className="space-y-3">
                {section.fields.map((f) => (
                  <Field
                    key={f.slug}
                    field={f}
                    name={`${section.key}-${f.slug}`}
                    value={props.inlineValues[f.slug]}
                    onChange={(v) => props.onInlineChange(f.slug, v)}
                  />
                ))}
              </div>
              <button
                type="button"
                onClick={props.onCloseModal}
                className="w-full rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
              >
                Done
              </button>
            </div>
          </div>
        ) : null}
      </div>
    );
  }

  // 1:1 inline.
  return (
    <div className="space-y-3 border-t pt-4">
      {heading}
      {section.fields.map((f) => (
        <Field
          key={f.slug}
          field={f}
          name={`${section.key}-${f.slug}`}
          value={props.inlineValues[f.slug]}
          onChange={(v) => props.onInlineChange(f.slug, v)}
        />
      ))}
    </div>
  );
}

function Field({
  field,
  value,
  onChange,
  name,
}: {
  field: PublicFormField;
  value: unknown;
  onChange: (v: unknown) => void;
  // Radio-group name. Must be unique per field *instance* — the same picklist
  // repeats across 1:M table rows, so callers scope this by section/row.
  name?: string;
}) {
  const str = value == null ? "" : String(value);
  const groupName = name ?? field.slug;
  const placeholder = field.placeholder ?? undefined;
  const labelText = (
    <>
      {field.label}
      {field.required ? <span className="ml-0.5 text-destructive">*</span> : null}
    </>
  );

  const control = () => {
    switch (field.field_type) {
      case "long_text":
        return (
          <textarea className={inputClass} rows={4} required={field.required} placeholder={placeholder} value={str} onChange={(e) => onChange(e.target.value)} />
        );
      case "boolean":
        return <input type="checkbox" checked={Boolean(value)} onChange={(e) => onChange(e.target.checked)} />;
      case "integer":
      case "bigint":
      case "numeric":
        return (
          <input
            type="number"
            className={inputClass}
            required={field.required}
            placeholder={placeholder}
            value={str}
            onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
          />
        );
      case "date":
        return <input type="date" className={inputClass} required={field.required} value={str} onChange={(e) => onChange(e.target.value)} />;
      case "timestamptz":
        return <input type="datetime-local" className={inputClass} required={field.required} value={str} onChange={(e) => onChange(e.target.value)} />;
      case "picklist":
        if (field.display === "radio") {
          return (
            <div className="space-y-1.5">
              {field.options.map((o) => (
                <label key={o} className="flex items-center gap-2 text-sm">
                  <input
                    type="radio"
                    name={groupName}
                    required={field.required}
                    checked={str === o}
                    value={o}
                    onChange={(e) => onChange(e.target.value)}
                  />
                  {o}
                </label>
              ))}
            </div>
          );
        }
        return (
          <select className={inputClass} required={field.required} value={str} onChange={(e) => onChange(e.target.value || null)}>
            <option value="">Select…</option>
            {field.options.map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>
        );
      default:
        return <input type="text" className={inputClass} required={field.required} placeholder={placeholder} value={str} onChange={(e) => onChange(e.target.value)} />;
    }
  };

  if (field.field_type === "boolean") {
    return (
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium">{labelText}</span>
        {control()}
      </div>
    );
  }
  return (
    <div>
      <label className="mb-1 block text-sm font-medium">{labelText}</label>
      {control()}
      {field.help_text ? <p className="mt-1 text-xs text-muted-foreground">{field.help_text}</p> : null}
    </div>
  );
}
