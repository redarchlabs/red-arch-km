"use client";

import { Plus, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  readTransform,
  rowsToTransform,
  transformToRows,
  type TransformRow,
} from "@/components/workflows/transform";

interface TransformEditorProps {
  data: Record<string, unknown>;
  patch: (next: Record<string, unknown>) => void;
}

/**
 * Edit a `script` task's `data.transform` map as a list of `{ variable,
 * expression }` rows. Each expression cell holds JSON text — a JsonLogic object
 * like `{ "var": "after.total" }`, a literal (`42`, `true`), or plain text kept
 * as a string. Seeds its rows once, so remount it (via a `key` on the node id)
 * to load a different node's transform.
 */
export function TransformEditor({ data, patch }: TransformEditorProps) {
  const [rows, setRows] = useState<TransformRow[]>(
    // Seed once; the editor owns row state thereafter.
    useMemo(() => transformToRows(readTransform(data)), []), // eslint-disable-line react-hooks/exhaustive-deps
  );

  const commit = (next: TransformRow[]) => {
    setRows(next);
    patch({ transform: rowsToTransform(next) });
  };

  const updateRow = (index: number, p: Partial<TransformRow>) =>
    commit(rows.map((r, i) => (i === index ? { ...r, ...p } : r)));
  const addRow = () => commit([...rows, { key: "", expr: "" }]);
  const removeRow = (index: number) => commit(rows.filter((_, i) => i !== index));

  return (
    <div className="space-y-2">
      {rows.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          No variables set. Each row assigns a run variable from an expression.
        </p>
      ) : null}
      {rows.map((row, index) => (
        <div key={index} className="flex flex-wrap items-center gap-1.5">
          <Input
            value={row.key}
            onChange={(e) => updateRow(index, { key: e.target.value })}
            placeholder="variable"
            className="h-9 w-28"
          />
          <span className="text-xs text-muted-foreground">=</span>
          <Input
            value={row.expr}
            onChange={(e) => updateRow(index, { expr: e.target.value })}
            placeholder='{ "var": "after.total" }'
            className="h-9 flex-1 min-w-[140px] font-mono text-xs"
          />
          <Button variant="ghost" size="icon" onClick={() => removeRow(index)} aria-label="Remove variable">
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      ))}
      <Button variant="outline" size="sm" onClick={addRow}>
        <Plus className="h-4 w-4" />
        Add variable
      </Button>
      <p className="text-xs text-muted-foreground">
        Expressions are JsonLogic evaluated against {"{ before, after, vars }"}. A value that isn&rsquo;t
        valid JSON is stored as plain text.
      </p>
    </div>
  );
}
