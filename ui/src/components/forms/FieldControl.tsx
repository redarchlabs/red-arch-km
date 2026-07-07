"use client";

import type { FieldDisplay, FieldMeta } from "@/lib/api/forms";

/**
 * Renders the input control for one entity field. The control is chosen by the
 * field's own `field_type` (never author-chosen); presentational props (label,
 * required, read-only, placeholder, picklist display) come from the form element.
 * Shared by the public intake page, the authenticated fill page, and the builder
 * preview via `FormRenderer`.
 */
export interface FieldControlProps {
  meta: FieldMeta;
  label: string;
  required: boolean;
  readOnly?: boolean;
  placeholder?: string;
  display?: FieldDisplay | null;
  value: unknown;
  onChange: (v: unknown) => void;
  /** Radio-group name — must be unique per field instance (scoped by row/section). */
  name?: string;
}

const inputClass =
  "w-full rounded-md border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring disabled:cursor-not-allowed disabled:opacity-60";

export function FieldControl({
  meta,
  label,
  required,
  readOnly = false,
  placeholder,
  display,
  value,
  onChange,
  name,
}: FieldControlProps) {
  const str = value == null ? "" : String(value);
  const groupName = name ?? meta.slug;
  const labelText = (
    <>
      {label}
      {required ? <span className="ml-0.5 text-destructive">*</span> : null}
    </>
  );

  const control = () => {
    switch (meta.field_type) {
      case "long_text":
        return (
          <textarea
            className={inputClass}
            rows={4}
            required={required}
            disabled={readOnly}
            placeholder={placeholder}
            value={str}
            onChange={(e) => onChange(e.target.value)}
          />
        );
      case "boolean":
        return (
          <input
            type="checkbox"
            disabled={readOnly}
            checked={Boolean(value)}
            onChange={(e) => onChange(e.target.checked)}
          />
        );
      case "integer":
      case "bigint":
      case "numeric":
        return (
          <input
            type="number"
            className={inputClass}
            required={required}
            disabled={readOnly}
            placeholder={placeholder}
            value={str}
            onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
          />
        );
      case "date":
        return (
          <input
            type="date"
            className={inputClass}
            required={required}
            disabled={readOnly}
            value={str.slice(0, 10)}
            onChange={(e) => onChange(e.target.value)}
          />
        );
      case "timestamptz":
        return (
          <input
            type="datetime-local"
            className={inputClass}
            required={required}
            disabled={readOnly}
            value={str}
            onChange={(e) => onChange(e.target.value)}
          />
        );
      case "picklist":
        if (display === "radio") {
          return (
            <div className="space-y-1.5">
              {meta.options.map((o) => (
                <label key={o} className="flex items-center gap-2 text-sm">
                  <input
                    type="radio"
                    name={groupName}
                    required={required}
                    disabled={readOnly}
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
          <select
            className={inputClass}
            required={required}
            disabled={readOnly}
            value={str}
            onChange={(e) => onChange(e.target.value || null)}
          >
            <option value="">Select…</option>
            {meta.options.map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>
        );
      default:
        return (
          <input
            type="text"
            className={inputClass}
            required={required}
            disabled={readOnly}
            placeholder={placeholder}
            value={str}
            onChange={(e) => onChange(e.target.value)}
          />
        );
    }
  };

  if (meta.field_type === "boolean") {
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
    </div>
  );
}
