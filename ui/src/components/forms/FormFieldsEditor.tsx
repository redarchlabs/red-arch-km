"use client";

/**
 * FormFieldsEditor — the root-entity field builder for an intake form.
 *
 * Replaces a flat "tick the fields you want" list with an ordered, reorderable
 * one: the array order IS the on-form order (the backend renders fields in
 * config order), and each field carries presentational overrides — label,
 * required, help text, placeholder, column width, and an optional group
 * heading rendered above it. Only entity fields not already added remain
 * available in the "Add field" picker.
 *
 * Pure/controlled: never mutates the incoming array — every change produces a
 * new `FormFieldConfig[]` via `onChange`.
 */
import { ArrowDown, ArrowUp, Trash2 } from "lucide-react";

import { Input } from "@/components/ui/input";
import type { EntityField } from "@/lib/api/entities";
import type { FieldWidth, FormFieldConfig } from "@/lib/api/forms";

interface FormFieldsEditorProps {
  entityFields: EntityField[];
  fields: FormFieldConfig[];
  onChange: (fields: FormFieldConfig[]) => void;
}

const selectClass =
  "h-8 rounded-md border bg-background px-2 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

export function FormFieldsEditor({ entityFields, fields, onChange }: FormFieldsEditorProps) {
  const bySlug = new Map(entityFields.map((f) => [f.slug, f]));
  const usedSlugs = new Set(fields.map((f) => f.slug));
  const available = entityFields.filter((f) => !usedSlugs.has(f.slug));

  const updateAt = (index: number, patch: Partial<FormFieldConfig>) =>
    onChange(fields.map((f, i) => (i === index ? { ...f, ...patch } : f)));

  const removeAt = (index: number) => onChange(fields.filter((_, i) => i !== index));

  const move = (index: number, dir: -1 | 1) => {
    const target = index + dir;
    if (target < 0 || target >= fields.length) return;
    const next = [...fields];
    [next[index], next[target]] = [next[target], next[index]];
    onChange(next);
  };

  const add = (slug: string) => {
    const field = bySlug.get(slug);
    if (!field) return;
    onChange([
      ...fields,
      { slug, label: field.name, required: field.is_required, width: "full" },
    ]);
  };

  return (
    <div className="space-y-3">
      {fields.length === 0 ? (
        <p className="rounded-md border border-dashed px-3 py-6 text-center text-sm text-muted-foreground">
          No fields on this form yet. Add one below.
        </p>
      ) : (
        <ul className="space-y-2">
          {fields.map((field, index) => {
            const meta = bySlug.get(field.slug);
            return (
              <li key={field.slug} className="space-y-2 rounded-md border p-3">
                <div className="flex items-center gap-2">
                  <div className="flex flex-col">
                    <button
                      type="button"
                      onClick={() => move(index, -1)}
                      disabled={index === 0}
                      aria-label="Move field up"
                      className="text-muted-foreground hover:text-foreground disabled:opacity-30"
                    >
                      <ArrowUp className="h-3.5 w-3.5" />
                    </button>
                    <button
                      type="button"
                      onClick={() => move(index, 1)}
                      disabled={index === fields.length - 1}
                      aria-label="Move field down"
                      className="text-muted-foreground hover:text-foreground disabled:opacity-30"
                    >
                      <ArrowDown className="h-3.5 w-3.5" />
                    </button>
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">
                      {meta?.name ?? field.slug}
                      <span className="ml-2 text-xs font-normal text-muted-foreground">
                        {meta?.field_type ?? "unknown field"}
                      </span>
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => removeAt(index)}
                    aria-label={`Remove ${meta?.name ?? field.slug}`}
                    className="text-muted-foreground hover:text-destructive"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>

                <Input
                  value={field.label ?? ""}
                  onChange={(e) => updateAt(index, { label: e.target.value || null })}
                  placeholder="Label shown to the user"
                  className="h-8 text-sm"
                />

                <div className="flex flex-wrap items-center gap-3">
                  <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
                    <input
                      type="checkbox"
                      checked={field.required ?? meta?.is_required ?? false}
                      onChange={(e) => updateAt(index, { required: e.target.checked })}
                    />
                    Required
                  </label>
                  <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
                    Width
                    <select
                      value={field.width ?? "full"}
                      onChange={(e) => updateAt(index, { width: e.target.value as FieldWidth })}
                      className={selectClass}
                    >
                      <option value="full">Full</option>
                      <option value="half">Half</option>
                    </select>
                  </label>
                </div>

                <Input
                  value={field.placeholder ?? ""}
                  onChange={(e) => updateAt(index, { placeholder: e.target.value || null })}
                  placeholder="Placeholder text (optional)"
                  className="h-8 text-xs"
                />
                <Input
                  value={field.help_text ?? ""}
                  onChange={(e) => updateAt(index, { help_text: e.target.value || null })}
                  placeholder="Help text (optional)"
                  className="h-8 text-xs"
                />
                <Input
                  value={field.heading ?? ""}
                  onChange={(e) => updateAt(index, { heading: e.target.value || null })}
                  placeholder="Group heading above this field (optional)"
                  className="h-8 text-xs"
                />
              </li>
            );
          })}
        </ul>
      )}

      {available.length > 0 ? (
        <label className="flex items-center gap-2 text-sm">
          <span className="text-muted-foreground">Add field</span>
          <select
            value=""
            onChange={(e) => {
              if (e.target.value) add(e.target.value);
            }}
            className={`${selectClass} h-9 flex-1`}
          >
            <option value="">Choose a field…</option>
            {available.map((f) => (
              <option key={f.slug} value={f.slug}>
                {f.name} ({f.field_type})
              </option>
            ))}
          </select>
        </label>
      ) : (
        <p className="text-xs text-muted-foreground">All entity fields are on the form.</p>
      )}
    </div>
  );
}
