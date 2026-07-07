"use client";

import { type Node } from "@xyflow/react";
import { Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { ACTION_CONFIG_FIELDS, ACTION_LABELS, ACTION_TYPES } from "@/components/workflows/actionTypes";
import { CasesEditor, type CaseItem } from "@/components/workflows/CasesEditor";
import { ConditionEditor } from "@/components/workflows/ConditionEditor";
import { DecisionTableEditor } from "@/components/workflows/DecisionTableEditor";
import { routingMode, toCasesMode, toConditionMode } from "@/components/workflows/gatewayRouting";
import { HttpRequestFields } from "@/components/workflows/HttpRequestFields";
import {
  EVENT_POSITIONS,
  EVENT_TYPE_LABELS,
  EVENT_TYPES,
  GATEWAY_LABELS,
  GATEWAY_TYPES,
  subtypeLabel,
  TASK_LABELS,
  TASK_TYPES,
  WAIT_TASK_TYPES,
  type TaskType,
} from "@/components/workflows/nodes/nodeMeta";
import { RecordFieldEditor } from "@/components/workflows/RecordFieldEditor";
import {
  applyContinueOnError,
  applyRetry,
  DEFAULT_RETRY,
  MAX_ATTEMPTS_CAP,
  normalizeRetry,
  readRetry,
  type RetryPolicy,
} from "@/components/workflows/retryPolicy";
import { TransformEditor } from "@/components/workflows/TransformEditor";
import type { Connection } from "@/lib/api/connections";
import type { EntityDefinition, EntityField } from "@/lib/api/entities";
import type { Form } from "@/lib/api/forms";

interface NodeInspectorProps {
  node: Node | null;
  /** All canvas nodes — powers the boundary event's host (attached_to) picker. */
  nodes?: Node[];
  /** Fields of the entity this workflow fires on (condition + trigger pickers). */
  fields?: EntityField[];
  /** All entities in the org (target picker for the create_record action). */
  entities?: EntityDefinition[];
  /** Org's intake forms (picker for the send_form action). */
  forms?: Form[];
  /** Org connections (picker for the http_request action's authenticated call). */
  connections?: Connection[];
  onChangeData: (id: string, data: Record<string, unknown>) => void;
  onDelete: (id: string) => void;
}

const OPERATIONS = ["create", "update", "delete"] as const;
const selectClass = "h-9 w-full rounded-md border bg-background px-2 text-sm";

export function NodeInspector({ node, nodes, fields, entities, forms, connections, onChangeData, onDelete }: NodeInspectorProps) {
  if (!node) {
    return (
      <div className="rounded-lg border bg-card p-4 text-sm text-muted-foreground">
        Select a node to edit it, or drag from a handle to connect nodes. The green handle is the
        true branch of a condition; the red handle is false.
      </div>
    );
  }

  const data = (node.data ?? {}) as Record<string, unknown>;
  const patch = (next: Record<string, unknown>) => onChangeData(node.id, { ...data, ...next });

  return (
    <div className="space-y-4 rounded-lg border bg-card p-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">{subtypeLabel({ type: node.type, data })}</h3>
        {node.type !== "trigger" ? (
          <Button variant="ghost" size="icon" onClick={() => onDelete(node.id)} aria-label="Delete node">
            <Trash2 className="h-4 w-4" />
          </Button>
        ) : null}
      </div>

      {node.type === "trigger" ? (
        <TriggerFields data={data} patch={patch} fields={fields} />
      ) : node.type === "condition" ? (
        <div className="space-y-1">
          <label className="text-xs font-medium text-muted-foreground">When</label>
          {/* Key to node.id so the editor's rawMode/rawText don't leak across node
              selection and silently discard another node's custom JsonLogic. */}
          <ConditionEditor
            key={node.id}
            expr={data.expr}
            fields={fields}
            onChange={(expr) => patch({ expr })}
          />
        </div>
      ) : node.type === "switch" ? (
        <SwitchFields data={data} patch={patch} fields={fields} />
      ) : node.type === "delay" ? (
        <DelayFields data={data} patch={patch} />
      ) : node.type === "task" ? (
        <TaskFields
          nodeId={node.id}
          data={data}
          patch={patch}
          onChangeData={onChangeData}
          entities={entities}
          forms={forms}
          connections={connections}
          triggerFields={fields}
        />
      ) : node.type === "gateway" ? (
        <GatewayFields nodeId={node.id} data={data} patch={patch} onChangeData={onChangeData} fields={fields} />
      ) : node.type === "event" ? (
        <EventFields data={data} patch={patch} nodes={nodes} />
      ) : (
        <ActionFields
          nodeId={node.id}
          data={data}
          patch={patch}
          onChangeData={onChangeData}
          entities={entities}
          forms={forms}
          connections={connections}
          triggerFields={fields}
        />
      )}
    </div>
  );
}

function TaskFields({
  nodeId,
  data,
  patch,
  onChangeData,
  entities,
  forms,
  connections,
  triggerFields,
}: {
  nodeId: string;
  data: Record<string, unknown>;
  patch: (next: Record<string, unknown>) => void;
  /** Full-replace path (store's updateNodeData) — lets the retry editor DELETE keys. */
  onChangeData: (id: string, data: Record<string, unknown>) => void;
  entities?: EntityDefinition[];
  forms?: Form[];
  connections?: Connection[];
  triggerFields?: EntityField[];
}) {
  const taskType = ((data.task_type as string | undefined) ?? "service") as TaskType;
  const isWait = WAIT_TASK_TYPES.includes(taskType);
  return (
    <div className="space-y-3">
      <div>
        <label className="text-xs font-medium text-muted-foreground">Task type</label>
        <select
          value={taskType}
          onChange={(e) => patch({ task_type: e.target.value })}
          className={`${selectClass} mt-1`}
        >
          {TASK_TYPES.map((t) => (
            <option key={t} value={t}>
              {TASK_LABELS[t]}
            </option>
          ))}
        </select>
      </div>
      {isWait ? (
        <>
          <p className="text-xs text-muted-foreground">
            A wait-state task — the run parks here until an external signal (a user completes it, a
            message arrives, or a called flow finishes).
          </p>
          {taskType === "user" || taskType === "manual" ? (
            <UserTaskFields data={data} patch={patch} />
          ) : null}
        </>
      ) : taskType === "businessRule" ? (
        <DecisionTableEditor key={nodeId} data={data} patch={patch} fields={triggerFields} />
      ) : taskType === "script" ? (
        <TransformEditor key={nodeId} data={data} patch={patch} />
      ) : (
        <ActionFields
          nodeId={nodeId}
          data={data}
          patch={patch}
          onChangeData={onChangeData}
          entities={entities}
          forms={forms}
          connections={connections}
          triggerFields={triggerFields}
        />
      )}
      <RetryFields data={data} onReplace={(next) => onChangeData(nodeId, next)} />
    </div>
  );
}

function UserTaskFields({
  data,
  patch,
}: {
  data: Record<string, unknown>;
  patch: (next: Record<string, unknown>) => void;
}) {
  const label = (data.label as string | undefined) ?? "";
  const assignee = (data.assignee as string | undefined) ?? "";
  return (
    <div className="space-y-2">
      <div>
        <label className="text-xs font-medium text-muted-foreground">Label</label>
        <Input
          value={label}
          onChange={(e) => patch({ label: e.target.value || undefined })}
          placeholder="Approve request"
          className="mt-1"
        />
      </div>
      <div>
        <label className="text-xs font-medium text-muted-foreground">Assignee</label>
        <Input
          value={assignee}
          onChange={(e) => patch({ assignee: e.target.value || undefined })}
          placeholder="user@example.com or a role"
          className="mt-1"
        />
        <p className="mt-1 text-xs text-muted-foreground">
          Who this task is presented to (an email, user id, or role — resolved by the run engine).
        </p>
      </div>
    </div>
  );
}

function RetryFields({
  data,
  onReplace,
}: {
  data: Record<string, unknown>;
  /** Replace the node's whole data object (retry edits add/remove keys wholesale). */
  onReplace: (next: Record<string, unknown>) => void;
}) {
  const retry = readRetry(data);
  const enabled = retry !== null;
  const continueOnError = data.continue_on_error === true;

  const setField = (key: keyof RetryPolicy, value: string) =>
    onReplace(applyRetry(data, normalizeRetry({ ...(retry ?? DEFAULT_RETRY), [key]: value })));

  return (
    <div className="space-y-2 border-t pt-3">
      <label className="flex items-center gap-1.5 text-sm font-medium">
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => onReplace(applyRetry(data, e.target.checked ? (retry ?? DEFAULT_RETRY) : null))}
        />
        Retry on failure
      </label>

      {enabled && retry ? (
        <div className="space-y-2 pl-5">
          <div>
            <label className="text-xs font-medium text-muted-foreground">Max attempts</label>
            <Input
              type="number"
              min={1}
              max={MAX_ATTEMPTS_CAP}
              value={retry.max_attempts}
              onChange={(e) => setField("max_attempts", e.target.value)}
              className="mt-1 h-9 w-24"
            />
          </div>
          <div className="flex gap-2">
            <div>
              <label className="text-xs font-medium text-muted-foreground">Base delay (s)</label>
              <Input
                type="number"
                min={0}
                value={retry.base_delay_seconds}
                onChange={(e) => setField("base_delay_seconds", e.target.value)}
                className="mt-1 h-9 w-24"
              />
            </div>
            <div>
              <label className="text-xs font-medium text-muted-foreground">Max delay (s)</label>
              <Input
                type="number"
                min={0}
                value={retry.max_delay_seconds}
                onChange={(e) => setField("max_delay_seconds", e.target.value)}
                className="mt-1 h-9 w-24"
              />
            </div>
          </div>
          <p className="text-xs text-muted-foreground">
            Full-jitter exponential back-off between attempts, capped at the max delay.
          </p>
        </div>
      ) : null}

      <label className="flex items-center gap-1.5 text-sm">
        <input
          type="checkbox"
          checked={continueOnError}
          onChange={(e) => onReplace(applyContinueOnError(data, e.target.checked))}
        />
        Continue the workflow if this task ultimately fails
      </label>
    </div>
  );
}

function GatewayFields({
  nodeId,
  data,
  patch,
  onChangeData,
  fields,
}: {
  nodeId: string;
  data: Record<string, unknown>;
  patch: (next: Record<string, unknown>) => void;
  /** Full-replace path — routing-mode switches DELETE the stale expr/cases key. */
  onChangeData: (id: string, data: Record<string, unknown>) => void;
  fields?: EntityField[];
}) {
  const gatewayType = (data.gateway_type as string | undefined) ?? "exclusive";
  return (
    <div className="space-y-3">
      <div>
        <label className="text-xs font-medium text-muted-foreground">Gateway type</label>
        <select
          value={gatewayType}
          onChange={(e) => patch({ gateway_type: e.target.value })}
          className={`${selectClass} mt-1`}
        >
          {GATEWAY_TYPES.map((g) => (
            <option key={g} value={g}>
              {GATEWAY_LABELS[g]}
            </option>
          ))}
        </select>
      </div>
      {gatewayType === "exclusive" ? (
        <ExclusiveRouting
          nodeId={nodeId}
          data={data}
          patch={patch}
          onReplace={(next) => onChangeData(nodeId, next)}
          fields={fields}
        />
      ) : gatewayType === "event_based" ? (
        <p className="text-xs text-muted-foreground">
          Waits, then routes to whichever catch event or receive task fires first.
        </p>
      ) : (
        <p className="text-xs text-muted-foreground">
          Forks a token down every outgoing branch (and joins when ≥2 branches arrive). No condition —
          the split/join is structural.
        </p>
      )}
    </div>
  );
}

function ExclusiveRouting({
  nodeId,
  data,
  patch,
  onReplace,
  fields,
}: {
  nodeId: string;
  data: Record<string, unknown>;
  patch: (next: Record<string, unknown>) => void;
  onReplace: (next: Record<string, unknown>) => void;
  fields?: EntityField[];
}) {
  const mode = routingMode(data);
  const cases = (data.cases as CaseItem[] | undefined) ?? [];
  return (
    <div className="space-y-2">
      <div>
        <label className="text-xs font-medium text-muted-foreground">Routing</label>
        <select
          value={mode}
          onChange={(e) => onReplace(e.target.value === "cases" ? toCasesMode(data) : toConditionMode(data))}
          className={`${selectClass} mt-1`}
        >
          <option value="condition">Two-way (true / false)</option>
          <option value="cases">Multi-way (cases)</option>
        </select>
      </div>
      {mode === "condition" ? (
        <div className="space-y-1">
          <label className="text-xs font-medium text-muted-foreground">
            Branch condition (true / false)
          </label>
          <ConditionEditor key={nodeId} expr={data.expr} fields={fields} onChange={(expr) => patch({ expr })} />
          <p className="text-xs text-muted-foreground">
            The true branch runs when the condition holds; wire a false branch for everything else.
          </p>
        </div>
      ) : (
        <CasesEditor cases={cases} fields={fields} onChange={(next) => patch({ cases: next })} />
      )}
    </div>
  );
}

function EventFields({
  data,
  patch,
  nodes,
}: {
  data: Record<string, unknown>;
  patch: (next: Record<string, unknown>) => void;
  nodes?: Node[];
}) {
  const position = (data.position as string | undefined) ?? "intermediate";
  const eventType = (data.event_type as string | undefined) ?? "none";
  const hostCandidates = (nodes ?? []).filter((n) => n.type === "task");
  return (
    <div className="space-y-3">
      <div>
        <label className="text-xs font-medium text-muted-foreground">Position</label>
        <select
          value={position}
          onChange={(e) => patch({ position: e.target.value })}
          className={`${selectClass} mt-1`}
        >
          {EVENT_POSITIONS.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
      </div>
      <div>
        <label className="text-xs font-medium text-muted-foreground">Event type</label>
        <select
          value={eventType}
          onChange={(e) => patch({ event_type: e.target.value })}
          className={`${selectClass} mt-1`}
        >
          {EVENT_TYPES.map((t) => (
            <option key={t} value={t}>
              {EVENT_TYPE_LABELS[t]}
            </option>
          ))}
        </select>
      </div>
      {eventType === "timer" && position === "intermediate" ? (
        <div>
          <label className="text-xs font-medium text-muted-foreground">Delay (seconds)</label>
          <Input
            type="number"
            min={0}
            value={Number(data.delay_seconds ?? 0) || ""}
            onChange={(e) => patch({ delay_seconds: Math.max(0, Math.floor(Number(e.target.value) || 0)) })}
            placeholder="60"
            className="mt-1 h-9 w-32"
          />
          <p className="mt-1 text-xs text-muted-foreground">
            The token waits here for this long, then continues from the next node.
          </p>
        </div>
      ) : null}
      {eventType === "error" && (position === "boundary" || position === "end") ? (
        <div>
          <label className="text-xs font-medium text-muted-foreground">Error code (optional)</label>
          <Input
            value={(data.error_code as string | undefined) ?? ""}
            onChange={(e) => patch({ error_code: e.target.value || undefined })}
            placeholder="payment_failed"
            className="mt-1"
          />
          <p className="mt-1 text-xs text-muted-foreground">
            {position === "boundary"
              ? "Only catches errors thrown with this code; blank catches any error."
              : "Ends the flow by throwing this error code for a matching boundary catcher."}
          </p>
        </div>
      ) : null}
      {position === "boundary" ? (
        <>
          <div>
            <label className="text-xs font-medium text-muted-foreground">Attached to</label>
            <select
              value={(data.attached_to as string | undefined) ?? ""}
              onChange={(e) => patch({ attached_to: e.target.value || undefined })}
              className={`${selectClass} mt-1`}
            >
              <option value="">Choose a task…</option>
              {hostCandidates.map((n) => (
                <option key={n.id} value={n.id}>
                  {subtypeLabel({ type: n.type, data: n.data as Record<string, unknown> })} ({n.id})
                </option>
              ))}
            </select>
          </div>
          <label className="flex items-center gap-1.5 text-sm">
            <input
              type="checkbox"
              checked={data.interrupting !== false}
              onChange={(e) => patch({ interrupting: e.target.checked })}
            />
            Interrupting (cancels the host task when it fires)
          </label>
          <p className="text-xs text-muted-foreground">
            The attachment (and its position on the host) applies after you save and reload.
          </p>
        </>
      ) : null}
    </div>
  );
}

function TriggerFields({
  data,
  patch,
  fields,
}: {
  data: Record<string, unknown>;
  patch: (next: Record<string, unknown>) => void;
  fields?: EntityField[];
}) {
  const operations = (data.operations as string[] | undefined) ?? [];
  const fieldFilter = (data.field_filter as string[] | undefined) ?? [];

  const toggleOp = (op: string) => {
    const next = operations.includes(op)
      ? operations.filter((o) => o !== op)
      : [...operations, op];
    patch({ operations: next });
  };

  const toggleField = (slug: string) => {
    const next = fieldFilter.includes(slug)
      ? fieldFilter.filter((f) => f !== slug)
      : [...fieldFilter, slug];
    patch({ field_filter: next });
  };

  const source = (data.source as string | undefined) ?? "any";
  const schedule = (data.schedule as { every_minutes?: number } | undefined) ?? {};
  const everyMinutes = Number(schedule.every_minutes ?? 0);

  return (
    <div className="space-y-3">
      <div>
        <label className="text-xs font-medium text-muted-foreground">Fire on</label>
        <div className="mt-1 flex gap-3">
          {OPERATIONS.map((op) => (
            <label key={op} className="flex items-center gap-1.5 text-sm">
              <input type="checkbox" checked={operations.includes(op)} onChange={() => toggleOp(op)} />
              {op}
            </label>
          ))}
        </div>
      </div>
      <div>
        <label className="text-xs font-medium text-muted-foreground">
          Only when these fields change (optional)
        </label>
        {fields && fields.length > 0 ? (
          <div className="mt-1 flex flex-col gap-1">
            {fields.map((f) => (
              <label key={f.slug} className="flex items-center gap-1.5 text-sm">
                <input
                  type="checkbox"
                  checked={fieldFilter.includes(f.slug)}
                  onChange={() => toggleField(f.slug)}
                />
                {f.name}
              </label>
            ))}
          </div>
        ) : (
          <Input
            value={fieldFilter.join(", ")}
            onChange={(e) =>
              patch({
                field_filter: e.target.value
                  .split(",")
                  .map((s) => s.trim())
                  .filter(Boolean),
              })
            }
            placeholder="status, amount"
            className="mt-1"
          />
        )}
      </div>
      <div>
        <label className="text-xs font-medium text-muted-foreground">Change source</label>
        <select
          value={source}
          onChange={(e) => patch({ source: e.target.value })}
          className={`${selectClass} mt-1`}
        >
          <option value="any">Any change (edits + form submissions)</option>
          <option value="form">Only intake-form submissions</option>
        </select>
      </div>
      <div>
        <label className="text-xs font-medium text-muted-foreground">Also run on a schedule</label>
        <div className="mt-1 flex items-center gap-2 text-sm">
          <span className="text-muted-foreground">every</span>
          <Input
            type="number"
            min={0}
            value={everyMinutes || ""}
            onChange={(e) => {
              const n = Math.max(0, Math.floor(Number(e.target.value) || 0));
              patch({ schedule: n > 0 ? { every_minutes: n } : undefined });
            }}
            placeholder="0"
            className="h-9 w-24"
          />
          <span className="text-muted-foreground">minutes (0 = off)</span>
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          Scheduled runs have no changed record — use them with actions like &ldquo;Create a
          record&rdquo;, &ldquo;Send an email&rdquo;, or &ldquo;Send a webhook&rdquo;. To run
          <em> only</em> on a schedule, uncheck all &ldquo;Fire on&rdquo; operations above.
        </p>
      </div>
    </div>
  );
}

function SwitchFields({
  data,
  patch,
  fields,
}: {
  data: Record<string, unknown>;
  patch: (next: Record<string, unknown>) => void;
  fields?: EntityField[];
}) {
  const cases = (data.cases as CaseItem[] | undefined) ?? [];
  return <CasesEditor cases={cases} fields={fields} onChange={(next) => patch({ cases: next })} />;
}

const DELAY_UNITS: Record<string, number> = { minutes: 60, hours: 3600, days: 86400 };

function DelayFields({
  data,
  patch,
}: {
  data: Record<string, unknown>;
  patch: (next: Record<string, unknown>) => void;
}) {
  const amount = Number(data.delay_amount ?? 0);
  const unit = (data.delay_unit as string | undefined) ?? "minutes";

  const setDelay = (nextAmount: number, nextUnit: string) => {
    const seconds = Math.max(0, Math.floor(nextAmount)) * (DELAY_UNITS[nextUnit] ?? 60);
    patch({ delay_amount: nextAmount, delay_unit: nextUnit, delay_seconds: seconds });
  };

  return (
    <div className="space-y-2">
      <label className="text-xs font-medium text-muted-foreground">Wait for</label>
      <div className="flex items-center gap-2">
        <Input
          type="number"
          min={0}
          value={amount || ""}
          onChange={(e) => setDelay(Number(e.target.value) || 0, unit)}
          placeholder="30"
          className="h-9 w-24"
        />
        <select value={unit} onChange={(e) => setDelay(amount, e.target.value)} className={selectClass}>
          {Object.keys(DELAY_UNITS).map((u) => (
            <option key={u} value={u}>
              {u}
            </option>
          ))}
        </select>
      </div>
      <p className="text-xs text-muted-foreground">
        The run pauses here, then continues from the next node once the wait elapses.
      </p>
    </div>
  );
}

function ActionFields({
  nodeId,
  data,
  patch,
  onChangeData,
  entities,
  forms,
  connections,
  triggerFields,
}: {
  nodeId: string;
  data: Record<string, unknown>;
  patch: (next: Record<string, unknown>) => void;
  /** Full-replace path — the http_request editor prunes emptied keys wholesale. */
  onChangeData: (id: string, data: Record<string, unknown>) => void;
  entities?: EntityDefinition[];
  forms?: Form[];
  connections?: Connection[];
  /** Fields of the entity the workflow fires on — the "from trigger" source. */
  triggerFields?: EntityField[];
}) {
  const actionType = (data.action_type as string | undefined) ?? "";
  const config = (data.config as Record<string, unknown> | undefined) ?? {};
  const fields = ACTION_CONFIG_FIELDS[actionType] ?? [];

  const setConfig = (key: string, value: unknown) => patch({ config: { ...config, [key]: value } });

  return (
    <div className="space-y-3">
      <div>
        <label className="text-xs font-medium text-muted-foreground">Action</label>
        <select
          value={actionType}
          onChange={(e) => patch({ action_type: e.target.value, config: {} })}
          className={selectClass}
        >
          <option value="">Choose action…</option>
          {ACTION_TYPES.map((t) => (
            <option key={t} value={t}>
              {ACTION_LABELS[t]}
            </option>
          ))}
        </select>
      </div>

      {actionType === "http_request" ? (
        // The connector editor prunes emptied config keys, so it must write
        // through the store's whole-object replace — a shallow patch would
        // re-add the very key it just removed.
        <HttpRequestFields
          nodeId={nodeId}
          data={data}
          connections={connections}
          onReplace={(next) => onChangeData(nodeId, next)}
        />
      ) : null}

      {fields.map((field) => {
        // A json field can key its editor to the entity chosen in another field
        // (e.g. create_record's values follow the selected target entity).
        const targetSlug = field.entityFieldsFrom
          ? String(config[field.entityFieldsFrom] ?? "")
          : "";
        const targetFields = field.entityFieldsFrom
          ? entities?.find((e) => e.slug === targetSlug)?.fields
          : undefined;
        const current = String(config[field.key] ?? "");

        return (
          <div key={field.key}>
            <label className="text-xs font-medium text-muted-foreground">{field.label}</label>
            {field.type === "textarea" ? (
              <Textarea
                value={current}
                onChange={(e) => setConfig(field.key, e.target.value)}
                placeholder={field.placeholder}
                rows={5}
                className="mt-1"
              />
            ) : field.type === "form" ? (
              <select
                value={current}
                onChange={(e) => setConfig(field.key, e.target.value)}
                className={`${selectClass} mt-1`}
              >
                <option value="">Choose form…</option>
                {(forms ?? []).map((f) => (
                  <option key={f.id} value={f.id}>
                    {f.name}
                  </option>
                ))}
              </select>
            ) : field.type === "trigger_field" ? (
              <select
                value={current}
                onChange={(e) => setConfig(field.key, e.target.value)}
                className={`${selectClass} mt-1`}
              >
                <option value="">Choose field…</option>
                {(triggerFields ?? []).map((f) => (
                  <option key={f.slug} value={f.slug}>
                    {f.name}
                  </option>
                ))}
              </select>
            ) : field.type === "entity" ? (
              entities && entities.length > 0 ? (
                <select
                  value={current}
                  onChange={(e) => setConfig(field.key, e.target.value)}
                  className={`${selectClass} mt-1`}
                >
                  <option value="">Choose entity…</option>
                  {entities.map((ent) => (
                    <option key={ent.slug} value={ent.slug}>
                      {ent.name}
                    </option>
                  ))}
                  {current && !entities.some((e) => e.slug === current) ? (
                    <option value={current}>{current}</option>
                  ) : null}
                </select>
              ) : (
                <Input
                  value={current}
                  onChange={(e) => setConfig(field.key, e.target.value)}
                  placeholder={field.placeholder}
                  className="mt-1"
                />
              )
            ) : field.type === "json" ? (
              <div className="mt-1">
                <RecordFieldEditor
                  key={`${nodeId}:${actionType}:${field.key}:${targetSlug}`}
                  value={config[field.key]}
                  fields={targetFields}
                  sourceFields={field.entityFieldsFrom ? triggerFields : undefined}
                  onChange={(obj) => setConfig(field.key, obj)}
                  emptyLabel="No values set."
                  addLabel="Add value"
                />
              </div>
            ) : (
              <Input
                value={current}
                onChange={(e) => setConfig(field.key, e.target.value)}
                placeholder={field.placeholder}
                className="mt-1"
              />
            )}
            {field.help ? <p className="mt-1 text-xs text-muted-foreground">{field.help}</p> : null}
          </div>
        );
      })}
    </div>
  );
}
