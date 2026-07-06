"use client";

import { Input } from "@/components/ui/input";
import type { EntityField } from "@/lib/api/entities";
import { isNumericField } from "@/components/workflows/recordFields";

interface FieldValueInputProps {
  field?: EntityField;
  value: string;
  onChange: (value: string) => void;
  className?: string;
  placeholder?: string;
}

const selectClass = "h-9 rounded-md border bg-background px-2 text-sm";

/**
 * A value input that adapts to an entity field's type: a dropdown for picklists
 * and booleans, a native date/number input where appropriate, plain text
 * otherwise. Always emits a string; callers coerce to the real type on save.
 */
export function FieldValueInput({ field, value, onChange, className, placeholder }: FieldValueInputProps) {
  if (field?.field_type === "picklist" && field.picklist_options && field.picklist_options.length > 0) {
    return (
      <select
        className={`${selectClass} ${className ?? ""}`}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">—</option>
        {field.picklist_options.map((opt) => (
          <option key={opt} value={opt}>
            {opt}
          </option>
        ))}
      </select>
    );
  }

  if (field?.field_type === "boolean") {
    return (
      <select
        className={`${selectClass} ${className ?? ""}`}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">—</option>
        <option value="true">true</option>
        <option value="false">false</option>
      </select>
    );
  }

  const type = isNumericField(field) ? "number" : field?.field_type === "date" ? "date" : "text";
  return (
    <Input
      type={type}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      className={className}
    />
  );
}
