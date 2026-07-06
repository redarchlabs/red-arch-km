"use client";

import { Braces, Plus, Rows3, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import type { EntityField } from "@/lib/api/entities";
import { FieldValueInput } from "@/components/workflows/FieldValueInput";
import { fieldMap, objectToRows, rowsToObject, type RecordRow } from "@/components/workflows/recordFields";

interface RecordFieldEditorProps {
  /** The stored value: a flat object, a JSON string, or null/undefined. */
  value: unknown;
  onChange: (value: Record<string, unknown>) => void;
  /** Entity fields — drives the field picker and type-aware value inputs. */
  fields?: EntityField[];
  /**
   * Fields of the triggering record. When provided, each row can switch from a
   * literal to "from trigger" and pick one of these to copy at run time.
   */
  sourceFields?: EntityField[];
  /** Noun for the trigger source in the mode toggle (e.g. "trigger"). */
  sourceLabel?: string;
  emptyLabel?: string;
  addLabel?: string;
}

const selectClass = "h-9 rounded-md border bg-background px-2 text-sm";

function safeParseObject(text: string): Record<string, unknown> | null {
  const trimmed = text.trim();
  if (trimmed === "") return {};
  try {
    const parsed = JSON.parse(trimmed);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

/**
 * Edit a flat record of field → value as friendly rows instead of raw JSON.
 * When `fields` is supplied the key becomes a dropdown of real entity fields and
 * the value input adapts to each field's type. An "Advanced (JSON)" escape hatch
 * remains for nested or exotic shapes.
 *
 * Holds its own row state, so remount (via a `key` prop) to reset it for a
 * different target.
 */
export function RecordFieldEditor({
  value,
  onChange,
  fields,
  sourceFields,
  sourceLabel = "trigger",
  emptyLabel,
  addLabel,
}: RecordFieldEditorProps) {
  // Seed once from the incoming value; the editor owns its rows thereafter.
  const initialRows = useMemo(() => objectToRows(value), []); // eslint-disable-line react-hooks/exhaustive-deps
  const [rows, setRows] = useState<RecordRow[]>(initialRows ?? []);
  const [rawMode, setRawMode] = useState(initialRows === null);
  const [rawText, setRawText] = useState(() => JSON.stringify(value ?? {}, null, 2));
  const [rawError, setRawError] = useState<string | null>(null);

  const map = useMemo(() => fieldMap(fields), [fields]);

  const commit = (next: RecordRow[]) => {
    setRows(next);
    onChange(rowsToObject(next, fields));
  };

  const updateRow = (index: number, patch: Partial<RecordRow>) =>
    commit(rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  const addRow = () => commit([...rows, { key: "", value: "" }]);
  const removeRow = (index: number) => commit(rows.filter((_, i) => i !== index));

  const applyRaw = () => {
    const parsed = safeParseObject(rawText);
    if (parsed === null) {
      setRawError("Enter a valid JSON object");
      return;
    }
    setRawError(null);
    onChange(parsed);
    const asRows = objectToRows(parsed);
    if (asRows) setRows(asRows);
  };

  if (rawMode) {
    return (
      <div className="space-y-2">
        <Textarea
          value={rawText}
          onChange={(e) => setRawText(e.target.value)}
          onBlur={applyRaw}
          rows={5}
          className="font-mono text-xs"
        />
        {rawError ? <p className="text-xs text-destructive">{rawError}</p> : null}
        <button
          type="button"
          className="flex items-center gap-1 text-xs text-muted-foreground underline"
          onClick={() => {
            const parsed = safeParseObject(rawText);
            const asRows = parsed === null ? null : objectToRows(parsed);
            if (asRows) {
              setRows(asRows);
              setRawMode(false);
              setRawError(null);
            } else {
              setRawError("This value is too complex for the simple editor");
            }
          }}
        >
          <Rows3 className="h-3 w-3" /> Simple editor
        </button>
      </div>
    );
  }

  const fieldOptions = fields ?? [];
  const sourceOptions = sourceFields ?? [];

  return (
    <div className="space-y-2">
      {rows.length === 0 ? (
        <p className="text-xs text-muted-foreground">{emptyLabel ?? "No fields set."}</p>
      ) : null}
      {rows.map((row, index) => {
        const field = map.get(row.key.trim());
        const isRef = row.ref !== undefined;
        return (
          <div key={index} className="flex flex-wrap items-center gap-1.5">
            {fieldOptions.length > 0 ? (
              <select
                className={`${selectClass} flex-1 min-w-[110px]`}
                value={row.key}
                onChange={(e) => updateRow(index, { key: e.target.value })}
              >
                <option value="">Choose field…</option>
                {fieldOptions.map((f) => (
                  <option key={f.slug} value={f.slug}>
                    {f.name}
                  </option>
                ))}
                {row.key && !map.has(row.key) ? <option value={row.key}>{row.key}</option> : null}
              </select>
            ) : (
              <Input
                value={row.key}
                onChange={(e) => updateRow(index, { key: e.target.value })}
                placeholder="field"
                className="h-9 flex-1 min-w-[110px]"
              />
            )}
            {sourceOptions.length > 0 ? (
              <select
                className={`${selectClass} w-[104px]`}
                value={isRef ? "ref" : "literal"}
                aria-label="Value source"
                onChange={(e) =>
                  updateRow(
                    index,
                    e.target.value === "ref"
                      ? { ref: row.ref ?? sourceOptions[0]?.slug ?? "" }
                      : { ref: undefined },
                  )
                }
              >
                <option value="literal">Literal</option>
                <option value="ref">From {sourceLabel}</option>
              </select>
            ) : null}
            {isRef ? (
              <select
                className={`${selectClass} w-28`}
                value={row.ref ?? ""}
                aria-label={`${sourceLabel} field`}
                onChange={(e) => updateRow(index, { ref: e.target.value })}
              >
                <option value="">Choose field…</option>
                {sourceOptions.map((f) => (
                  <option key={f.slug} value={f.slug}>
                    {f.name}
                  </option>
                ))}
              </select>
            ) : (
              <FieldValueInput
                field={field}
                value={row.value}
                onChange={(v) => updateRow(index, { value: v })}
                placeholder="value"
                className="h-9 w-28"
              />
            )}
            <Button variant="ghost" size="icon" onClick={() => removeRow(index)} aria-label="Remove field">
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
        );
      })}
      <div className="flex items-center justify-between">
        <Button variant="outline" size="sm" onClick={addRow}>
          <Plus className="h-4 w-4" />
          {addLabel ?? "Add field"}
        </Button>
        <button
          type="button"
          className="flex items-center gap-1 text-xs text-muted-foreground underline"
          onClick={() => {
            setRawText(JSON.stringify(rowsToObject(rows, fields), null, 2));
            setRawError(null);
            setRawMode(true);
          }}
        >
          <Braces className="h-3 w-3" /> Advanced (JSON)
        </button>
      </div>
    </div>
  );
}
