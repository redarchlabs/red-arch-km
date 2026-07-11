"use client";

import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  createAgent,
  updateAgent,
  type Agent,
  type AgentCreateInput,
  type AgentKind,
  type ProviderInfo,
} from "@/lib/api/agents";
import { getApiErrorMessage } from "@/lib/api/errors";

const selectClass = "h-9 w-full rounded-md border bg-background px-2 text-sm";
const KINDS: AgentKind[] = ["operator", "advisory", "coordinator"];

interface WorkflowOption {
  id: string;
  name: string;
}

interface AgentDialogProps {
  editing: Agent | "create";
  providers: ProviderInfo[];
  agents: Agent[];
  workflows: WorkflowOption[];
  onClose: () => void;
  onSaved: () => void;
}

export function AgentDialog({ editing, providers, agents, workflows, onClose, onSaved }: AgentDialogProps) {
  const initial = editing === "create" ? null : editing;
  const [name, setName] = useState(initial?.name ?? "");
  const [displayName, setDisplayName] = useState(initial?.display_name ?? "");
  const [kind, setKind] = useState<AgentKind>(initial?.kind ?? "operator");
  const [provider, setProvider] = useState(initial?.provider ?? providers[0]?.name ?? "openai");
  const [model, setModel] = useState(initial?.model ?? "");
  const [supervisorId, setSupervisorId] = useState(initial?.supervisor_id ?? "");
  const [persona, setPersona] = useState(initial?.persona ?? "");
  const [canRunWorkflows, setCanRunWorkflows] = useState(
    initial?.grants?.tools?.includes("run_workflow") ?? false,
  );
  const [recordsWrite, setRecordsWrite] = useState(initial?.grants?.records_write ?? false);
  const [approveWorkflows, setApproveWorkflows] = useState(
    initial?.grants?.approval_required?.includes("run_workflow") ?? false,
  );
  const [allowlist, setAllowlist] = useState<string[]>(initial?.workflow_allowlist ?? []);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const models = useMemo(
    () => providers.find((p) => p.name === provider)?.models ?? [],
    [providers, provider],
  );

  const buildPayload = (): AgentCreateInput => ({
    name,
    display_name: displayName || null,
    kind,
    provider,
    model: model || models[0]?.id || "",
    supervisor_id: supervisorId || null,
    persona: persona || null,
    grants: {
      tools: canRunWorkflows ? ["run_workflow"] : [],
      records_write: recordsWrite,
      approval_required: approveWorkflows ? ["run_workflow"] : [],
    },
    workflow_allowlist: allowlist,
  });

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      if (editing === "create") {
        await createAgent(buildPayload());
      } else {
        const { name: _omit, ...update } = buildPayload();
        await updateAgent(editing.id, update);
      }
      onSaved();
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to save agent"));
    } finally {
      setSaving(false);
    }
  };

  const toggleWorkflow = (id: string) =>
    setAllowlist((prev) => (prev.includes(id) ? prev.filter((w) => w !== id) : [...prev, id]));

  return (
    <Dialog open onClose={onClose}>
      <DialogHeader>
        <DialogTitle>{editing === "create" ? "New agent" : `Edit ${editing.name}`}</DialogTitle>
      </DialogHeader>

      <div className="max-h-[65vh] space-y-3 overflow-y-auto px-1 py-2">
        {editing === "create" ? (
          <label className="block text-sm">
            Name (lowercase, hyphenated)
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="triage-bot" />
          </label>
        ) : null}
        <label className="block text-sm">
          Display name
          <Input value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
        </label>

        <div className="grid grid-cols-2 gap-3">
          <label className="block text-sm">
            Role
            <select className={selectClass} value={kind} onChange={(e) => setKind(e.target.value as AgentKind)}>
              {KINDS.map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </select>
          </label>
          <label className="block text-sm">
            Supervisor
            <select className={selectClass} value={supervisorId} onChange={(e) => setSupervisorId(e.target.value)}>
              <option value="">— none (top of chain) —</option>
              {agents
                .filter((a) => a.id !== initial?.id)
                .map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
            </select>
          </label>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <label className="block text-sm">
            Provider
            <select
              className={selectClass}
              value={provider}
              onChange={(e) => {
                setProvider(e.target.value);
                setModel("");
              }}
            >
              {providers.map((p) => (
                <option key={p.name} value={p.name}>
                  {p.label} {p.configured ? "" : "(no key)"}
                </option>
              ))}
            </select>
          </label>
          <label className="block text-sm">
            Model
            <select className={selectClass} value={model || models[0]?.id} onChange={(e) => setModel(e.target.value)}>
              {models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label}
                </option>
              ))}
            </select>
          </label>
        </div>

        <label className="block text-sm">
          Persona / instructions
          <textarea
            className="mt-1 h-24 w-full rounded-md border bg-background px-2 py-1 text-sm"
            value={persona}
            onChange={(e) => setPersona(e.target.value)}
            placeholder="You handle inbound support triage…"
          />
        </label>

        <fieldset className="rounded-md border p-2 text-sm">
          <legend className="px-1 text-xs text-muted-foreground">Capabilities</legend>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={canRunWorkflows} onChange={(e) => setCanRunWorkflows(e.target.checked)} />
            May run workflows
          </label>
          <label className="ml-6 flex items-center gap-2 text-muted-foreground">
            <input
              type="checkbox"
              checked={approveWorkflows}
              disabled={!canRunWorkflows}
              onChange={(e) => setApproveWorkflows(e.target.checked)}
            />
            Require human approval before running a workflow
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={recordsWrite} onChange={(e) => setRecordsWrite(e.target.checked)} />
            May write records
          </label>
        </fieldset>

        {canRunWorkflows ? (
          <fieldset className="rounded-md border p-2 text-sm">
            <legend className="px-1 text-xs text-muted-foreground">Permitted workflows</legend>
            {workflows.length === 0 ? (
              <p className="text-muted-foreground">No workflows yet.</p>
            ) : (
              <div className="max-h-32 space-y-1 overflow-y-auto">
                {workflows.map((w) => (
                  <label key={w.id} className="flex items-center gap-2">
                    <input type="checkbox" checked={allowlist.includes(w.id)} onChange={() => toggleWorkflow(w.id)} />
                    {w.name}
                  </label>
                ))}
              </div>
            )}
          </fieldset>
        ) : null}

        {error ? <p className="text-sm text-destructive">{error}</p> : null}
      </div>

      <DialogFooter>
        <Button variant="outline" onClick={onClose} disabled={saving}>
          Cancel
        </Button>
        <Button onClick={save} disabled={saving || (editing === "create" && !name)}>
          {saving ? "Saving…" : "Save"}
        </Button>
      </DialogFooter>
    </Dialog>
  );
}
