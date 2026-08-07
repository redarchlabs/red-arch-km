"use client";

import { Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import type { Agent } from "@/lib/api/agents";

const selectClass = "h-9 w-full rounded-md border bg-background px-2 text-sm";

const FIELD_TYPES = ["string", "number", "integer", "boolean", "array", "object"] as const;

interface SchemaFieldSpec {
  type?: string;
  enum?: unknown[];
  required?: boolean;
  maxLength?: number;
  description?: string;
}

/** Config panel for an `agent` task: which operator does the step, the task
 * prompt, and the structured output contract (`complete_task`'s schema). */
export function AgentTaskFields({
  data,
  patch,
  agents,
  workflowId,
}: {
  data: Record<string, unknown>;
  patch: (next: Record<string, unknown>) => void;
  agents?: Agent[];
  workflowId?: string;
}) {
  const agentId = (data.agent_id as string | undefined) ?? "";
  const task = (data.task as string | undefined) ?? "";
  const capture = (data.capture as string | undefined) ?? "";
  const allowWebResearch = data.allow_web_research === true;
  const schema = (data.output_schema ?? {}) as Record<string, SchemaFieldSpec>;

  const operators = (agents ?? []).filter((a) => a.kind === "operator" && a.enabled);
  const selected = operators.find((a) => a.id === agentId);
  const consented =
    selected == null ||
    workflowId == null ||
    (selected.workflow_invocable ?? []).includes("*") ||
    (selected.workflow_invocable ?? []).includes(workflowId);

  const patchSchema = (next: Record<string, SchemaFieldSpec>) => patch({ output_schema: next });

  const renameField = (from: string, to: string) => {
    if (!to || to === from || to in schema) return;
    const next: Record<string, SchemaFieldSpec> = {};
    for (const [key, value] of Object.entries(schema)) next[key === from ? to : key] = value;
    patchSchema(next);
  };

  return (
    <div className="space-y-3">
      <div>
        <label className="text-xs font-medium text-muted-foreground">Agent</label>
        <select value={agentId} onChange={(e) => patch({ agent_id: e.target.value || undefined })} className={`${selectClass} mt-1`}>
          <option value="">Choose an operator…</option>
          {operators.map((a) => (
            <option key={a.id} value={a.id}>
              {a.display_name || a.name}
            </option>
          ))}
        </select>
        {!consented ? (
          <p className="mt-1 text-xs text-amber-600">
            This agent has not opted in to this workflow — publish will be blocked until its
            &quot;workflow invocable&quot; list includes it.
          </p>
        ) : null}
      </div>

      <div>
        <label className="text-xs font-medium text-muted-foreground">Task</label>
        <Textarea
          value={task}
          onChange={(e) => patch({ task: e.target.value || undefined })}
          placeholder={"Triage this ticket: {{ after.subject }}\n\n{{ after.body }}"}
          rows={4}
          className="mt-1 font-mono text-xs"
        />
        <p className="mt-1 text-xs text-muted-foreground">
          Supports {"{{ after.* }} / {{ inputs.* }} / {{ vars.* }}"} templates. Interpolated record
          text is treated as data, not instructions — but avoid interpolating sensitive fields.
        </p>
      </div>

      <div className="space-y-1">
        <div className="flex items-center justify-between">
          <label className="text-xs font-medium text-muted-foreground">Structured output</label>
          <Button
            variant="ghost"
            size="sm"
            className="h-6 px-2 text-xs"
            onClick={() => patchSchema({ ...schema, [`field_${Object.keys(schema).length + 1}`]: { type: "string" } })}
          >
            <Plus className="mr-1 h-3 w-3" /> Field
          </Button>
        </div>
        {Object.keys(schema).length === 0 ? (
          <p className="text-xs text-muted-foreground">
            No fields yet — the agent must call complete_task with these fields to finish the step.
          </p>
        ) : (
          <div className="space-y-1.5">
            {Object.entries(schema).map(([name, raw]) => {
              const spec = typeof raw === "object" && raw !== null ? raw : { type: String(raw) };
              return (
                <div key={name} className="flex items-center gap-1.5">
                  <Input
                    defaultValue={name}
                    onBlur={(e) => renameField(name, e.target.value.trim())}
                    className="h-8 flex-1 font-mono text-xs"
                    aria-label="Field name"
                  />
                  <select
                    value={spec.type ?? "string"}
                    onChange={(e) => patchSchema({ ...schema, [name]: { ...spec, type: e.target.value } })}
                    className="h-8 w-24 rounded-md border bg-background px-1 text-xs"
                    aria-label="Field type"
                  >
                    {FIELD_TYPES.map((t) => (
                      <option key={t} value={t}>
                        {t}
                      </option>
                    ))}
                  </select>
                  <Input
                    value={Array.isArray(spec.enum) ? spec.enum.join(",") : ""}
                    onChange={(e) => {
                      const values = e.target.value
                        .split(",")
                        .map((v) => v.trim())
                        .filter(Boolean);
                      patchSchema({ ...schema, [name]: { ...spec, enum: values.length > 0 ? values : undefined } });
                    }}
                    placeholder="enum a,b,c"
                    className="h-8 w-28 text-xs"
                    aria-label="Allowed values"
                  />
                  <label className="flex items-center gap-1 text-xs text-muted-foreground">
                    <input
                      type="checkbox"
                      checked={spec.required !== false}
                      onChange={(e) => patchSchema({ ...schema, [name]: { ...spec, required: e.target.checked ? undefined : false } })}
                    />
                    req
                  </label>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7"
                    aria-label={`Remove ${name}`}
                    onClick={() => {
                      const next = { ...schema };
                      delete next[name];
                      patchSchema(next);
                    }}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
              );
            })}
          </div>
        )}
      </div>

      <div>
        <label className="text-xs font-medium text-muted-foreground">Capture as</label>
        <Input
          value={capture}
          onChange={(e) => patch({ capture: e.target.value.trim() || undefined })}
          placeholder="triage"
          className="mt-1"
        />
        <p className="mt-1 text-xs text-muted-foreground">
          Downstream nodes read the validated output as {"{{ vars.<name>.<field> }}"}.
        </p>
      </div>

      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={allowWebResearch} onChange={(e) => patch({ allow_web_research: e.target.checked || undefined })} />
        Allow web research
        <span className="text-xs text-muted-foreground">(off by default: un-approved outbound queries)</span>
      </label>

      <p className="rounded-md bg-muted/60 p-2 text-xs text-muted-foreground">
        Attach an <b>error boundary</b> for escalation/failure (required to publish) and a{" "}
        <b>timer boundary</b> as the SLA — when it fires, the agent run is cancelled and the flow
        takes the timer path.
      </p>
    </div>
  );
}
