"use client";

import { CheckCircle2, FlaskConical, XCircle } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { RecordFieldEditor } from "@/components/workflows/RecordFieldEditor";
import type { EntityField } from "@/lib/api/entities";
import type { WorkflowTestResult } from "@/lib/api/workflows";

interface TestPanelProps {
  running: boolean;
  result: WorkflowTestResult | null;
  fields?: EntityField[];
  onRun: (input: {
    operation: string;
    before: Record<string, unknown> | null;
    after: Record<string, unknown> | null;
  }) => void;
}

const selectClass = "h-9 rounded-md border bg-background px-2 text-sm";

/** Empty object → null, so the dry-run sees "no record state" not "{}". */
function orNull(record: Record<string, unknown>): Record<string, unknown> | null {
  return Object.keys(record).length > 0 ? record : null;
}

export function TestPanel({ running, result, fields, onRun }: TestPanelProps) {
  const [operation, setOperation] = useState("update");
  const [before, setBefore] = useState<Record<string, unknown>>({});
  const [after, setAfter] = useState<Record<string, unknown>>({ status: "closed" });

  const run = () => {
    onRun({
      operation,
      before: operation === "create" ? null : orNull(before),
      after: operation === "delete" ? null : orNull(after),
    });
  };

  return (
    <div className="space-y-3 rounded-lg border bg-card p-4">
      <div className="flex items-center gap-2">
        <FlaskConical className="h-4 w-4 text-muted-foreground" />
        <h3 className="text-sm font-semibold">Test (dry run)</h3>
      </div>
      <p className="text-xs text-muted-foreground">
        Simulate a record change against the saved version. No data is written.
      </p>

      <div className="flex items-center gap-2">
        <label className="text-xs font-medium text-muted-foreground">Operation</label>
        <select value={operation} onChange={(e) => setOperation(e.target.value)} className={selectClass}>
          <option value="create">create</option>
          <option value="update">update</option>
          <option value="delete">delete</option>
        </select>
      </div>

      {operation !== "create" ? (
        <div>
          <label className="text-xs font-medium text-muted-foreground">
            Before {operation === "delete" ? "(the record being deleted)" : "(the record's prior state)"}
          </label>
          <div className="mt-1">
            <RecordFieldEditor value={before} fields={fields} onChange={setBefore} emptyLabel="Empty record." />
          </div>
        </div>
      ) : null}

      {operation !== "delete" ? (
        <div>
          <label className="text-xs font-medium text-muted-foreground">
            After {operation === "create" ? "(the new record)" : "(the record after the change)"}
          </label>
          <div className="mt-1">
            <RecordFieldEditor value={after} fields={fields} onChange={setAfter} emptyLabel="Empty record." />
          </div>
        </div>
      ) : null}

      <Button size="sm" onClick={run} disabled={running}>
        {running ? "Running…" : "Run test"}
      </Button>

      {result ? <TestResultView result={result} /> : null}
    </div>
  );
}

function TestResultView({ result }: { result: WorkflowTestResult }) {
  return (
    <div className="space-y-2 rounded-md border bg-muted/40 p-3 text-xs">
      {result.error ? (
        <p className="flex items-center gap-1.5 text-destructive">
          <XCircle className="h-4 w-4" /> {result.error}
        </p>
      ) : (
        <p className="flex items-center gap-1.5 font-medium">
          {result.conditions_matched ? (
            <CheckCircle2 className="h-4 w-4 text-emerald-500" />
          ) : (
            <XCircle className="h-4 w-4 text-muted-foreground" />
          )}
          {result.conditions_matched ? "Conditions matched — actions would run" : "Conditions did not match"}
        </p>
      )}

      {result.condition_trace.length > 0 ? (
        <div>
          <div className="font-medium text-muted-foreground">Conditions</div>
          {result.condition_trace.map((t) => (
            <div key={t.node_id} className="flex items-center gap-1.5">
              {t.result ? (
                <CheckCircle2 className="h-3 w-3 text-emerald-500" />
              ) : (
                <XCircle className="h-3 w-3 text-rose-500" />
              )}
              <span className="font-mono">{t.node_id}</span>
            </div>
          ))}
        </div>
      ) : null}

      {result.steps.length > 0 ? (
        <div>
          <div className="font-medium text-muted-foreground">Would run</div>
          {result.steps.map((s, i) => (
            <div key={i} className="mt-1 rounded bg-background p-2">
              <div className="font-medium">{s.action_type}</div>
              <pre className="overflow-x-auto text-[11px] text-muted-foreground">
                {JSON.stringify(s.simulated_output, null, 2)}
              </pre>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
