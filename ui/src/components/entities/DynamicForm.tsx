"use client";

import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import type { EntityField } from "@/lib/api/entities";

/** One selectable target record for a relationship picker. */
export interface RelationshipOption {
  value: string;
  label: string;
}

/** A to-one relationship rendered as a record picker in the form. */
export interface RelationshipFormField {
  id: string;
  slug: string;
  name: string;
  is_required: boolean;
  targetEntityName: string;
  options: RelationshipOption[];
}

interface DynamicFormProps {
  fields: EntityField[];
  relationships?: RelationshipFormField[];
  relationshipsLoading?: boolean;
  initial?: Record<string, unknown>;
  submitLabel?: string;
  busy?: boolean;
  error?: string | null;
  onSubmit: (data: Record<string, unknown>) => void;
  onCancel?: () => void;
}

/** Map a field definition array to controlled inputs and emit a coerced payload. */
export function DynamicForm({
  fields,
  relationships,
  relationshipsLoading = false,
  initial,
  submitLabel = "Save",
  busy = false,
  error,
  onSubmit,
  onCancel,
}: DynamicFormProps) {
  const rels = useMemo(() => relationships ?? [], [relationships]);
  const initialValues = useMemo(
    () => toStringMap(fields, rels, initial),
    [fields, rels, initial],
  );
  const [values, setValues] = useState<Record<string, string>>(initialValues);
  const [localError, setLocalError] = useState<string | null>(null);

  const setValue = (slug: string, value: string) =>
    setValues((prev) => ({ ...prev, [slug]: value }));

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return;
    try {
      onSubmit(coerce(fields, rels, values));
      setLocalError(null);
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : "Invalid input");
    }
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      {fields.map((field) => (
        <div key={field.id} className="space-y-1">
          <label className="text-sm font-medium" htmlFor={`f-${field.id}`}>
            {field.name}
            {field.is_required ? <span className="text-destructive"> *</span> : null}
          </label>
          <FieldControl
            field={field}
            value={values[field.slug] ?? ""}
            onChange={(v) => setValue(field.slug, v)}
          />
        </div>
      ))}

      {rels.map((rel) => (
        <div key={rel.id} className="space-y-1">
          <label className="text-sm font-medium" htmlFor={`r-${rel.id}`}>
            {rel.name}
            {rel.is_required ? <span className="text-destructive"> *</span> : null}
          </label>
          <select
            id={`r-${rel.id}`}
            value={values[rel.slug] ?? ""}
            onChange={(e) => setValue(rel.slug, e.target.value)}
            disabled={relationshipsLoading}
            className="h-9 w-full rounded-md border bg-background px-3 text-sm"
          >
            <option value="">
              {relationshipsLoading ? "Loading…" : `— Select ${rel.targetEntityName} —`}
            </option>
            {rel.options.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
          {!relationshipsLoading && rel.options.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              No {rel.targetEntityName} records yet — create one first to link it here.
            </p>
          ) : null}
        </div>
      ))}

      {(localError ?? error) ? (
        <p className="text-sm text-destructive">{localError ?? error}</p>
      ) : null}

      <div className="flex gap-2">
        <Button type="submit" disabled={busy}>
          {submitLabel}
        </Button>
        {onCancel ? (
          <Button type="button" variant="ghost" onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
        ) : null}
      </div>
    </form>
  );
}

interface FieldControlProps {
  field: EntityField;
  value: string;
  onChange: (value: string) => void;
}

function FieldControl({ field, value, onChange }: FieldControlProps) {
  const id = `f-${field.id}`;
  const common = { id, value, onChange: (e: React.ChangeEvent<HTMLInputElement>) => onChange(e.target.value) };

  switch (field.field_type) {
    case "long_text":
    case "json":
      return (
        <Textarea
          id={id}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          rows={field.field_type === "json" ? 5 : 3}
          placeholder={field.field_type === "json" ? "{ }" : undefined}
        />
      );
    case "boolean":
      return (
        <select
          id={id}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="h-9 w-full rounded-md border bg-background px-3 text-sm"
        >
          <option value="">—</option>
          <option value="true">Yes</option>
          <option value="false">No</option>
        </select>
      );
    case "picklist":
      return (
        <select
          id={id}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="h-9 w-full rounded-md border bg-background px-3 text-sm"
        >
          <option value="">—</option>
          {(field.picklist_options ?? []).map((opt) => (
            <option key={opt} value={opt}>
              {opt}
            </option>
          ))}
        </select>
      );
    case "integer":
    case "bigint":
    case "numeric":
      return <Input type="number" step={field.field_type === "numeric" ? "any" : "1"} {...common} />;
    case "date":
      return <Input type="date" {...common} />;
    case "timestamptz":
      return <Input type="datetime-local" {...common} />;
    default:
      return <Input type="text" {...common} />;
  }
}

function toStringMap(
  fields: EntityField[],
  relationships: RelationshipFormField[],
  initial: Record<string, unknown> | undefined,
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const field of fields) {
    const raw = initial?.[field.slug];
    out[field.slug] =
      raw === null || raw === undefined
        ? ""
        : typeof raw === "object"
          ? JSON.stringify(raw, null, 2)
          : String(raw);
  }
  for (const rel of relationships) {
    const raw = initial?.[rel.slug];
    out[rel.slug] = raw === null || raw === undefined ? "" : String(raw);
  }
  return out;
}

/** Coerce string form values back to typed values for the API payload. */
function coerce(
  fields: EntityField[],
  relationships: RelationshipFormField[],
  values: Record<string, string>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const field of fields) {
    const raw = (values[field.slug] ?? "").trim();
    if (raw === "") {
      if (field.is_required) throw new Error(`${field.name} is required`);
      out[field.slug] = null;
      continue;
    }
    switch (field.field_type) {
      case "integer":
      case "bigint": {
        const n = Number(raw);
        if (!Number.isInteger(n)) throw new Error(`${field.name} must be a whole number`);
        out[field.slug] = n;
        break;
      }
      case "numeric": {
        const n = Number(raw);
        if (Number.isNaN(n)) throw new Error(`${field.name} must be a number`);
        out[field.slug] = n;
        break;
      }
      case "boolean":
        out[field.slug] = raw === "true";
        break;
      case "json":
        try {
          out[field.slug] = JSON.parse(raw);
        } catch {
          throw new Error(`${field.name} must be valid JSON`);
        }
        break;
      default:
        out[field.slug] = raw;
    }
  }
  for (const rel of relationships) {
    const raw = (values[rel.slug] ?? "").trim();
    if (raw === "") {
      if (rel.is_required) throw new Error(`${rel.name} is required`);
      out[rel.slug] = null;
    } else {
      out[rel.slug] = raw;
    }
  }
  return out;
}
