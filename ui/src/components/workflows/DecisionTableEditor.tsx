"use client";

import { Plus, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { ConditionEditor } from "@/components/workflows/ConditionEditor";
import {
  asJsonLogic,
  emptyRule,
  HIT_POLICIES,
  readDecisionTable,
  writeDecisionTable,
  type DecisionRule,
  type HitPolicy,
} from "@/components/workflows/decisionTable";
import { newNodeId } from "@/components/workflows/graphSerde";
import { RecordFieldEditor } from "@/components/workflows/RecordFieldEditor";
import type { EntityField } from "@/lib/api/entities";

interface DecisionTableEditorProps {
  data: Record<string, unknown>;
  patch: (next: Record<string, unknown>) => void;
  /** Entity fields — power the per-rule condition editor's field pickers. */
  fields?: EntityField[];
}

/** A rule plus a stable client id used only for React keys / editor remounts. */
interface RuleDraft extends DecisionRule {
  id: string;
}

const HIT_POLICY_LABELS: Record<HitPolicy, string> = {
  first: "First match wins",
  collect: "Collect every match",
};

const selectClass = "h-9 w-full rounded-md border bg-background px-2 text-sm";

/**
 * Edit a `businessRule` task's decision table: a hit policy plus an ordered list
 * of rules, each a condition (`when`) and a map of output variables. Seeds its
 * row state once from `data`, so remount it (via a `key` on the node id) to load
 * a different node's table.
 */
export function DecisionTableEditor({ data, patch, fields }: DecisionTableEditorProps) {
  // Seed once; the editor owns hit-policy/rule state thereafter.
  const seed = useMemo(() => readDecisionTable(data), []); // eslint-disable-line react-hooks/exhaustive-deps
  const [hitPolicy, setHitPolicy] = useState<HitPolicy>(seed.hit_policy);
  const [rules, setRules] = useState<RuleDraft[]>(() =>
    seed.rules.map((r) => ({ ...r, id: newNodeId("rule") })),
  );

  const commit = (nextRules: RuleDraft[], nextPolicy: HitPolicy) => {
    setRules(nextRules);
    setHitPolicy(nextPolicy);
    patch({
      decision_table: writeDecisionTable({
        hit_policy: nextPolicy,
        rules: nextRules.map(({ when, output }) => ({ when, output })),
      }),
    });
  };

  const updateRule = (index: number, p: Partial<DecisionRule>) =>
    commit(rules.map((r, i) => (i === index ? { ...r, ...p } : r)), hitPolicy);
  const addRule = () => commit([...rules, { ...emptyRule(), id: newNodeId("rule") }], hitPolicy);
  const removeRule = (index: number) => commit(rules.filter((_, i) => i !== index), hitPolicy);

  return (
    <div className="space-y-3">
      <div>
        <label className="text-xs font-medium text-muted-foreground">Hit policy</label>
        <select
          value={hitPolicy}
          onChange={(e) => commit(rules, e.target.value as HitPolicy)}
          className={`${selectClass} mt-1`}
        >
          {HIT_POLICIES.map((p) => (
            <option key={p} value={p}>
              {HIT_POLICY_LABELS[p]}
            </option>
          ))}
        </select>
      </div>

      <p className="text-xs text-muted-foreground">
        Rules are checked top to bottom.{" "}
        {hitPolicy === "first"
          ? "The first matching rule's output is applied."
          : "Every matching rule's output is merged (later rules win a shared key)."}{" "}
        A rule with no conditions always matches — use it as a default at the bottom.
      </p>

      {rules.map((rule, index) => (
        <div key={rule.id} className="space-y-2 rounded-md border p-2">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-muted-foreground">Rule {index + 1}</span>
            <Button variant="ghost" size="icon" onClick={() => removeRule(index)} aria-label="Remove rule">
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
          <div className="space-y-1">
            <label className="text-xs font-medium text-muted-foreground">When</label>
            <ConditionEditor
              key={rule.id}
              expr={rule.when}
              fields={fields}
              onChange={(expr) => updateRule(index, { when: asJsonLogic(expr) })}
            />
          </div>
          <div className="space-y-1">
            <label className="text-xs font-medium text-muted-foreground">Set</label>
            <RecordFieldEditor
              key={rule.id}
              value={rule.output}
              onChange={(output) => updateRule(index, { output })}
              emptyLabel="No outputs — this rule sets nothing."
              addLabel="Add output"
            />
          </div>
        </div>
      ))}

      <Button variant="outline" size="sm" onClick={addRule}>
        <Plus className="h-4 w-4" />
        Add rule
      </Button>
    </div>
  );
}
