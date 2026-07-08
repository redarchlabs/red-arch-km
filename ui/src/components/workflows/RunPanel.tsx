"use client";

import { Play } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  ManualRunInputs,
  collectDeclaredInputs,
  missingRequiredInputs,
} from "@/components/workflows/ManualRunInputs";
import type { EntityField } from "@/lib/api/entities";
import { listRecords, type EntityRecord } from "@/lib/api/entityRecords";
import { getApiErrorMessage } from "@/lib/api/errors";
import {
  runWorkflow,
  updateWorkflow,
  type RunPermission,
  type RunPermissionMode,
  type TriggerInput,
} from "@/lib/api/workflows";

const selectClass = "h-9 w-full rounded-md border bg-background px-2 text-sm";

const PERMISSION_LABELS: Record<RunPermissionMode, string> = {
  org_admin: "Org admins only",
  any_member: "Any member of the org",
  roles: "Specific roles / groups",
};

function recordLabel(rec: EntityRecord): string {
  for (const key of ["name", "title", "email", "first_name"]) {
    if (typeof rec[key] === "string" && rec[key]) return rec[key] as string;
  }
  return rec.id.slice(0, 8);
}

interface Props {
  workflowId: string;
  entitySlug: string | null;
  fields: EntityField[];
  runPermission: RunPermission;
  onPermissionSaved: (p: RunPermission) => void;
  /** True if the workflow has a published version (manual run needs one). */
  canRun: boolean;
  /** True for a manual (on-demand) workflow — run it with declared input variables. */
  isManual?: boolean;
  /** The input variables the manual trigger declares (from the published version). */
  manualInputs?: TriggerInput[];
}

export function RunPanel({
  workflowId,
  entitySlug,
  fields,
  runPermission,
  onPermissionSaved,
  canRun,
  isManual = false,
  manualInputs = [],
}: Props) {
  const [operation, setOperation] = useState("update");
  const [records, setRecords] = useState<EntityRecord[]>([]);
  const [recordId, setRecordId] = useState("");
  const [inputValues, setInputValues] = useState<Record<string, unknown>>({});
  const [running, setRunning] = useState(false);
  const [perm, setPerm] = useState<RunPermission>(runPermission);
  const [savingPerm, setSavingPerm] = useState(false);

  useEffect(() => setPerm(runPermission), [runPermission]);

  const loadRecords = useCallback(async () => {
    if (!entitySlug) return;
    try {
      const r = await listRecords(entitySlug, { limit: 100 });
      setRecords(r.items);
      if (r.items.length > 0) setRecordId((prev) => prev || r.items[0].id);
    } catch {
      setRecords([]);
    }
  }, [entitySlug]);

  useEffect(() => {
    if (isManual) return;
    void loadRecords();
  }, [loadRecords, isManual]);

  const setInput = (key: string, value: unknown) => setInputValues((prev) => ({ ...prev, [key]: value }));

  const missingRequired = missingRequiredInputs(manualInputs, inputValues);

  const runManual = async () => {
    setRunning(true);
    try {
      // Only send declared keys; the backend coerces + re-validates against the schema.
      const result = await runWorkflow(workflowId, {
        inputs: collectDeclaredInputs(manualInputs, inputValues),
      });
      reportResult(result);
    } catch (e: unknown) {
      toast.error(getApiErrorMessage(e, "Run failed"));
    } finally {
      setRunning(false);
    }
  };

  const runAgainstRecord = async () => {
    setRunning(true);
    try {
      const record = records.find((r) => r.id === recordId);
      // Send the chosen record as the "after" state, keyed to its field slugs.
      const after: Record<string, unknown> = {};
      if (record) for (const f of fields) after[f.slug] = record[f.slug];
      const result = await runWorkflow(workflowId, {
        operation,
        record_id: recordId || null,
        before: after,
        after,
      });
      reportResult(result);
    } catch (e: unknown) {
      toast.error(getApiErrorMessage(e, "Run failed"));
    } finally {
      setRunning(false);
    }
  };

  const reportResult = (result: Awaited<ReturnType<typeof runWorkflow>>) => {
    if (result.status === "succeeded") {
      toast.success(`Ran — ${result.actions_executed} action(s) executed`);
    } else if (result.status === "waiting") {
      toast.info(`Ran — ${result.actions_executed} action(s), now waiting at a delay`);
    } else if (result.status === "skipped") {
      toast.info("Ran — conditions did not match, no actions executed");
    } else {
      toast.error(`Run ${result.status}: ${result.error ?? "see run history"}`);
    }
  };

  const savePermission = async (next: RunPermission) => {
    setSavingPerm(true);
    try {
      const wf = await updateWorkflow(workflowId, { run_permission: next });
      onPermissionSaved(wf.run_permission);
      toast.success("Run permission saved");
    } catch (e: unknown) {
      toast.error(getApiErrorMessage(e, "Failed to save permission"));
    } finally {
      setSavingPerm(false);
    }
  };

  return (
    <div className="space-y-4 rounded-lg border bg-card p-4">
      <div>
        <h3 className="text-sm font-semibold">Run now (real)</h3>
        <p className="text-xs text-muted-foreground">
          {isManual
            ? "Runs the published workflow on demand with the inputs you provide. Writes data and records a run."
            : "Executes the published workflow for real against the record you pick. Writes data and records a run."}
        </p>
      </div>

      {!canRun ? (
        <p className="text-xs text-muted-foreground">Publish the workflow first to run it.</p>
      ) : isManual ? (
        <div className="space-y-3">
          <ManualRunInputs inputs={manualInputs} values={inputValues} onChange={setInput} />
          <Button onClick={() => void runManual()} disabled={running || missingRequired} size="sm">
            <Play className="h-4 w-4" />
            {running ? "Running…" : "Run for real"}
          </Button>
        </div>
      ) : (
        <div className="space-y-2">
          <div className="flex gap-2">
            <select value={operation} onChange={(e) => setOperation(e.target.value)} className={selectClass}>
              {["create", "update", "delete"].map((o) => (
                <option key={o} value={o}>
                  {o}
                </option>
              ))}
            </select>
          </div>
          {records.length > 0 ? (
            <select value={recordId} onChange={(e) => setRecordId(e.target.value)} className={selectClass}>
              {records.map((r) => (
                <option key={r.id} value={r.id}>
                  {recordLabel(r)}
                </option>
              ))}
            </select>
          ) : (
            <p className="text-xs text-muted-foreground">No records in this entity to run against.</p>
          )}
          <Button onClick={() => void runAgainstRecord()} disabled={running || !recordId} size="sm">
            <Play className="h-4 w-4" />
            {running ? "Running…" : "Run for real"}
          </Button>
        </div>
      )}

      <div className="space-y-2 border-t pt-3">
        <label className="text-xs font-medium text-muted-foreground">Who can run this</label>
        <select
          value={perm.mode}
          onChange={(e) => {
            const next = { ...perm, mode: e.target.value as RunPermissionMode };
            setPerm(next);
            void savePermission(next);
          }}
          disabled={savingPerm}
          className={selectClass}
        >
          {(Object.keys(PERMISSION_LABELS) as RunPermissionMode[]).map((m) => (
            <option key={m} value={m}>
              {PERMISSION_LABELS[m]}
            </option>
          ))}
        </select>
        {perm.mode === "roles" ? (
          <>
            <input
              className={selectClass}
              placeholder="Role IDs (comma-separated)"
              defaultValue={perm.role_ids.join(", ")}
              onBlur={(e) => {
                const role_ids = e.target.value.split(",").map((s) => s.trim()).filter(Boolean);
                const next = { ...perm, role_ids };
                setPerm(next);
                void savePermission(next);
              }}
            />
            <input
              className={selectClass}
              placeholder="Group IDs (comma-separated)"
              defaultValue={perm.group_ids.join(", ")}
              onBlur={(e) => {
                const group_ids = e.target.value.split(",").map((s) => s.trim()).filter(Boolean);
                const next = { ...perm, group_ids };
                setPerm(next);
                void savePermission(next);
              }}
            />
            <p className="text-xs text-muted-foreground">Org admins can always run, regardless of this.</p>
          </>
        ) : null}
      </div>
    </div>
  );
}
