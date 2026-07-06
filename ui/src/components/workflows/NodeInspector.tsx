"use client";

import { type Node } from "@xyflow/react";
import { Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ACTION_CONFIG_FIELDS, ACTION_LABELS, ACTION_TYPES } from "@/components/workflows/actionTypes";
import { ConditionEditor } from "@/components/workflows/ConditionEditor";
import { RecordFieldEditor } from "@/components/workflows/RecordFieldEditor";
import type { EntityDefinition, EntityField } from "@/lib/api/entities";

interface NodeInspectorProps {
  node: Node | null;
  /** Fields of the entity this workflow fires on (condition + trigger pickers). */
  fields?: EntityField[];
  /** All entities in the org (target picker for the create_record action). */
  entities?: EntityDefinition[];
  onChangeData: (id: string, data: Record<string, unknown>) => void;
  onDelete: (id: string) => void;
}

const OPERATIONS = ["create", "update", "delete"] as const;
const selectClass = "h-9 w-full rounded-md border bg-background px-2 text-sm";

export function NodeInspector({ node, fields, entities, onChangeData, onDelete }: NodeInspectorProps) {
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
        <h3 className="text-sm font-semibold capitalize">{node.type} node</h3>
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
          <ConditionEditor expr={data.expr} fields={fields} onChange={(expr) => patch({ expr })} />
        </div>
      ) : (
        <ActionFields
          nodeId={node.id}
          data={data}
          patch={patch}
          entities={entities}
          triggerFields={fields}
        />
      )}
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
    </div>
  );
}

function ActionFields({
  nodeId,
  data,
  patch,
  entities,
  triggerFields,
}: {
  nodeId: string;
  data: Record<string, unknown>;
  patch: (next: Record<string, unknown>) => void;
  entities?: EntityDefinition[];
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
            {field.type === "entity" ? (
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
          </div>
        );
      })}
    </div>
  );
}
