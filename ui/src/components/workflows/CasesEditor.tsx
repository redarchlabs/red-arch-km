"use client";

import { Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ConditionEditor } from "@/components/workflows/ConditionEditor";
import { newNodeId } from "@/components/workflows/graphSerde";
import type { EntityField } from "@/lib/api/entities";

/** One branch of a multi-way route: a stable `handle` (edge `source_handle`), a
 * display `label`, and the JsonLogic `expr` that selects it. */
export interface CaseItem {
  handle: string;
  label: string;
  expr: unknown;
}

interface CasesEditorProps {
  cases: CaseItem[];
  fields?: EntityField[];
  onChange: (next: CaseItem[]) => void;
}

/**
 * A controlled editor for an ordered list of routing cases, shared by the legacy
 * switch node and the exclusive gateway's multi-way mode. First match wins;
 * anything matching no case follows the reserved `default` handle rendered by
 * `handlesFor`.
 */
export function CasesEditor({ cases, fields, onChange }: CasesEditorProps) {
  const addCase = () =>
    onChange([...cases, { handle: newNodeId("case"), label: `Case ${cases.length + 1}`, expr: null }]);
  const updateCase = (index: number, patch: Partial<CaseItem>) =>
    onChange(cases.map((c, i) => (i === index ? { ...c, ...patch } : c)));
  const removeCase = (index: number) => onChange(cases.filter((_, i) => i !== index));

  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">
        Routes to the first matching case (top to bottom). Anything that matches no case follows the
        <span className="font-medium"> default</span> handle. Connect each case handle to a branch.
      </p>
      {cases.map((c, i) => (
        <div key={c.handle} className="space-y-1 rounded-md border p-2">
          <div className="flex items-center gap-2">
            <Input
              value={c.label}
              onChange={(e) => updateCase(i, { label: e.target.value })}
              placeholder={`Case ${i + 1}`}
              className="h-8 flex-1"
            />
            <Button variant="ghost" size="icon" onClick={() => removeCase(i)} aria-label="Remove case">
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
          <ConditionEditor expr={c.expr} fields={fields} onChange={(expr) => updateCase(i, { expr })} />
        </div>
      ))}
      <Button variant="outline" size="sm" onClick={addCase}>
        <Plus className="h-4 w-4" />
        Add case
      </Button>
    </div>
  );
}
