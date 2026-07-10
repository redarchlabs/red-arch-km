"use client";

import { Plus, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { EntityField } from "@/lib/api/entities";
import type { RecordFilter, RecordFilterOp } from "@/lib/api/entityRecords";
import { nativeSelectClass } from "@/lib/nativeSelect";

const OP_LABELS: Record<RecordFilterOp, string> = {
  eq: "=",
  ne: "≠",
  gt: ">",
  gte: "≥",
  lt: "<",
  lte: "≤",
  contains: "contains",
  in: "in",
  isnull: "is empty",
};

/** Operators offered per field type (mirrors what the backend can index/coerce). */
function opsForField(field: EntityField | undefined): RecordFilterOp[] {
  switch (field?.field_type) {
    case "integer":
    case "bigint":
    case "numeric":
    case "date":
    case "timestamptz":
      return ["eq", "ne", "gt", "gte", "lt", "lte", "isnull"];
    case "picklist":
      return ["eq", "ne", "in", "isnull"];
    case "boolean":
      return ["eq", "ne", "isnull"];
    case "text":
    case "long_text":
      return ["contains", "eq", "ne", "isnull"];
    default:
      return ["eq", "ne", "isnull"];
  }
}

interface RecordFilterBarProps {
  fields: EntityField[];
  filters: RecordFilter[];
  onChange: (filters: RecordFilter[]) => void;
}

/** A row-per-filter builder (field · operator · value) for the records grid.
 * Emits the full filter list on any change; the grid decides which rows are
 * complete enough to send. */
export function RecordFilterBar({ fields, filters, onChange }: RecordFilterBarProps) {
  const update = (index: number, patch: Partial<RecordFilter>) =>
    onChange(filters.map((f, i) => (i === index ? { ...f, ...patch } : f)));
  const remove = (index: number) => onChange(filters.filter((_, i) => i !== index));
  const add = () => onChange([...filters, { field: fields[0]?.slug ?? "", op: "eq", value: "" }]);

  return (
    <div className="space-y-2">
      {filters.map((filter, index) => {
        const field = fields.find((f) => f.slug === filter.field);
        const allowed = opsForField(field);
        const showValue = filter.op !== "isnull";
        const isPicklistPick = field?.field_type === "picklist" && filter.op !== "in";
        return (
          <div key={index} className="flex flex-wrap items-center gap-2">
            <select
              className={nativeSelectClass}
              value={filter.field}
              aria-label="Filter field"
              onChange={(e) => {
                const nextField = fields.find((f) => f.slug === e.target.value);
                const ops = opsForField(nextField);
                update(index, {
                  field: e.target.value,
                  op: ops.includes(filter.op) ? filter.op : ops[0],
                });
              }}
            >
              {fields.map((f) => (
                <option key={f.id} value={f.slug}>
                  {f.name}
                </option>
              ))}
            </select>

            <select
              className={nativeSelectClass}
              value={filter.op}
              aria-label="Filter operator"
              onChange={(e) => update(index, { op: e.target.value as RecordFilterOp })}
            >
              {allowed.map((op) => (
                <option key={op} value={op}>
                  {OP_LABELS[op]}
                </option>
              ))}
            </select>

            {showValue &&
              (isPicklistPick ? (
                <select
                  className={nativeSelectClass}
                  value={filter.value ?? ""}
                  aria-label="Filter value"
                  onChange={(e) => update(index, { value: e.target.value })}
                >
                  <option value="">—</option>
                  {(field?.picklist_options ?? []).map((opt) => (
                    <option key={opt} value={opt}>
                      {opt}
                    </option>
                  ))}
                </select>
              ) : (
                <Input
                  className="h-9 w-48"
                  value={filter.value ?? ""}
                  placeholder={filter.op === "in" ? "a,b,c" : "value"}
                  aria-label="Filter value"
                  onChange={(e) => update(index, { value: e.target.value })}
                />
              ))}

            <Button variant="ghost" size="icon" onClick={() => remove(index)} aria-label="Remove filter">
              <X className="h-4 w-4" />
            </Button>
          </div>
        );
      })}

      <Button variant="outline" size="sm" onClick={add} disabled={fields.length === 0}>
        <Plus className="h-4 w-4" />
        Add filter
      </Button>
    </div>
  );
}
