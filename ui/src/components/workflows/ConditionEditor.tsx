"use client";

import { Plus, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  CONDITION_OPS,
  compileRows,
  parseExpr,
  type ConditionOp,
  type ConditionRow,
} from "@/components/workflows/conditionExpr";
import { FieldValueInput } from "@/components/workflows/FieldValueInput";
import { fieldMap, isNumericField } from "@/components/workflows/recordFields";
import type { EntityField } from "@/lib/api/entities";

interface ConditionEditorProps {
  expr: unknown;
  fields?: EntityField[];
  onChange: (expr: unknown) => void;
}

const selectClass = "h-9 rounded-md border bg-background px-2 text-sm";

/** Resolve a var path like "after.status" back to its entity field. */
function fieldForPath(map: Map<string, EntityField>, path: string): EntityField | undefined {
  const slug = path.replace(/^(before|after)\./, "");
  return map.get(slug);
}

export function ConditionEditor({ expr, fields, onChange }: ConditionEditorProps) {
  const parsed = useMemo(() => parseExpr(expr), [expr]);
  const map = useMemo(() => fieldMap(fields), [fields]);
  // If the stored expression can't be represented as rows, start in raw mode.
  const [rawMode, setRawMode] = useState(parsed === null);
  const [rawText, setRawText] = useState(() => JSON.stringify(expr ?? {}, null, 2));
  const [rawError, setRawError] = useState<string | null>(null);

  const rows: ConditionRow[] = parsed ?? [];

  // Thread each row's field type through so numeric fields coerce numbers while
  // text fields keep look-alike strings ("01234", order codes) intact.
  const setRows = (next: ConditionRow[]) =>
    onChange(compileRows(next, (path) => isNumericField(fieldForPath(map, path))));

  const updateRow = (index: number, patch: Partial<ConditionRow>) => {
    const next = rows.map((r, i) => (i === index ? { ...r, ...patch } : r));
    setRows(next);
  };

  const addRow = () => setRows([...rows, { field: "after.", op: "==", value: "" }]);
  const removeRow = (index: number) => setRows(rows.filter((_, i) => i !== index));

  const applyRaw = () => {
    try {
      const value = rawText.trim() === "" ? null : JSON.parse(rawText);
      setRawError(null);
      onChange(value);
    } catch {
      setRawError("Invalid JSON");
    }
  };

  if (rawMode) {
    return (
      <div className="space-y-2">
        <Textarea
          value={rawText}
          onChange={(e) => setRawText(e.target.value)}
          onBlur={applyRaw}
          rows={6}
          className="font-mono text-xs"
        />
        {rawError ? <p className="text-xs text-destructive">{rawError}</p> : null}
        <button
          type="button"
          className="text-xs text-muted-foreground underline"
          onClick={() => setRawMode(false)}
        >
          Switch to simple editor
        </button>
        <p className="text-xs text-muted-foreground">JsonLogic against {"{ before, after }"}.</p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {rows.length === 0 ? (
        <p className="text-xs text-muted-foreground">No conditions — the true branch always runs.</p>
      ) : null}
      {rows.map((row, index) => {
        const field = fieldForPath(map, row.field);
        return (
          <div key={index} className="flex flex-wrap items-center gap-1.5">
            <Input
              value={row.field}
              onChange={(e) => updateRow(index, { field: e.target.value })}
              placeholder="after.status"
              list={fields && fields.length > 0 ? "condition-field-paths" : undefined}
              className="h-9 flex-1 min-w-[120px]"
            />
            <select
              value={row.op}
              onChange={(e) => updateRow(index, { op: e.target.value as ConditionOp })}
              className={selectClass}
            >
              {CONDITION_OPS.map((op) => (
                <option key={op} value={op}>
                  {op}
                </option>
              ))}
            </select>
            {row.op === "in" ? (
              <Input
                value={row.value}
                onChange={(e) => updateRow(index, { value: e.target.value })}
                placeholder="a, b, c"
                className="h-9 w-24"
              />
            ) : (
              <FieldValueInput
                field={field}
                value={row.value}
                onChange={(v) => updateRow(index, { value: v })}
                placeholder="value"
                className="h-9 w-24"
              />
            )}
            <Button variant="ghost" size="icon" onClick={() => removeRow(index)} aria-label="Remove condition">
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
        );
      })}
      {fields && fields.length > 0 ? (
        <datalist id="condition-field-paths">
          {fields.flatMap((f) => [
            <option key={`after-${f.slug}`} value={`after.${f.slug}`}>
              {f.name} (after)
            </option>,
            <option key={`before-${f.slug}`} value={`before.${f.slug}`}>
              {f.name} (before)
            </option>,
          ])}
        </datalist>
      ) : null}
      <div className="flex items-center justify-between">
        <Button variant="outline" size="sm" onClick={addRow}>
          <Plus className="h-4 w-4" />
          Add condition
        </Button>
        <button
          type="button"
          className="text-xs text-muted-foreground underline"
          onClick={() => {
            setRawText(JSON.stringify(expr ?? {}, null, 2));
            setRawMode(true);
          }}
        >
          Advanced (JSON)
        </button>
      </div>
      {rows.length > 1 ? (
        <p className="text-xs text-muted-foreground">All conditions must match (AND).</p>
      ) : null}
    </div>
  );
}
