"use client";

import { ArrowLeft, Rocket, Save } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { useStore } from "zustand";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { FormsPanel } from "@/components/workflows/FormsPanel";
import { NodeInspector } from "@/components/workflows/NodeInspector";
import { RunPanel } from "@/components/workflows/RunPanel";
import { TestPanel } from "@/components/workflows/TestPanel";
import { WorkflowDesigner } from "@/components/workflows/designer/WorkflowDesigner";
import { useDesignerStore } from "@/components/workflows/designer/store";
import { normalizeForSave, starterGraph, toDefinition, toReactFlow } from "@/components/workflows/graphSerde";
import { hasErrors, validateGraph, type Issue } from "@/components/workflows/validation";
import { listEntities, type EntityDefinition, type EntityField } from "@/lib/api/entities";
import { getApiErrorMessage } from "@/lib/api/errors";
import { listForms, type Form } from "@/lib/api/forms";
import {
  getWorkflow,
  listVersions,
  publishVersion,
  saveDraft,
  testVersion,
  type Workflow,
  type WorkflowTestResult,
  type WorkflowVersion,
} from "@/lib/api/workflows";

export default function WorkflowDesignPage() {
  const { id } = useParams<{ id: string }>();

  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [entityName, setEntityName] = useState<string | null>(null);
  const [entitySlug, setEntitySlug] = useState<string | null>(null);
  const [entityFields, setEntityFields] = useState<EntityField[]>([]);
  const [entities, setEntities] = useState<EntityDefinition[]>([]);
  const [forms, setForms] = useState<Form[]>([]);
  const [baseVersion, setBaseVersion] = useState<WorkflowVersion | null>(null);
  const [savedVersion, setSavedVersion] = useState<WorkflowVersion | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [testResult, setTestResult] = useState<WorkflowTestResult | null>(null);
  const [testing, setTesting] = useState(false);
  const [issues, setIssues] = useState<Issue[]>([]);

  // Graph state (nodes/edges/selection/history) lives in the designer store, so
  // the palette/canvas/keymap/command-palette share one source of truth.
  const selected = useDesignerStore((s) => s.nodes.find((n) => n.selected) ?? null);
  const allNodes = useDesignerStore((s) => s.nodes);
  // "Unsaved changes" = the graph's structural signature moved since the last
  // save/load reset the undo baseline (selection/measurement don't count).
  const dirty = useStore(useDesignerStore.temporal, (t) => t.pastStates.length > 0);

  const errorCount = issues.filter((i) => i.severity === "error").length;
  const warningCount = issues.filter((i) => i.severity === "warning").length;

  // Monotonic request id: the App Router reuses this component across `id`
  // changes, so a slow load for an old id must not overwrite a newer one.
  const loadReq = useRef(0);

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    const reqId = ++loadReq.current;
    try {
      const [wf, versions] = await Promise.all([getWorkflow(id), listVersions(id)]);
      if (reqId !== loadReq.current) return; // a newer id superseded this load
      setWorkflow(wf);
      // Forms power the send_form action's picker; failure shouldn't block editing.
      const loadedForms = await listForms().catch(() => []);
      if (reqId !== loadReq.current) return;
      setForms(loadedForms);
      // Entities power the field pickers in the inspector and test panel (own
      // entity) and the create_record target picker (all entities). A failure
      // here shouldn't block editing, so fall back to free-form input.
      try {
        const all = await listEntities();
        if (reqId !== loadReq.current) return;
        setEntities(all);
        const own = all.find((e) => e.id === wf.entity_definition_id) ?? null;
        setEntityName(own?.name ?? null);
        setEntitySlug(own?.slug ?? null);
        setEntityFields(own?.fields ?? []);
      } catch {
        if (reqId !== loadReq.current) return;
        setEntities([]);
        setEntityName(null);
        setEntitySlug(null);
        setEntityFields([]);
      }
      const latest = versions[0] ?? null;
      const graph = latest ? toReactFlow(latest.definition) : starterGraph();
      useDesignerStore.getState().setGraph(graph.nodes, graph.edges);
      // Reset the undo baseline so a freshly-loaded graph reads as "not dirty".
      useDesignerStore.temporal.getState().clear();
      setBaseVersion(latest);
      // If the latest version is an unpublished draft, we keep editing it;
      // editing a published version forks a fresh draft on save.
      setSavedVersion(latest && latest.status === "draft" ? latest : null);
    } catch (e: unknown) {
      if (reqId !== loadReq.current) return;
      setError(getApiErrorMessage(e, "Failed to load workflow"));
    } finally {
      if (reqId === loadReq.current) setIsLoading(false);
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  // Clear the shared store when leaving the designer so the next workflow that
  // mounts doesn't briefly show this one's graph.
  useEffect(() => {
    return () => {
      useDesignerStore.getState().reset();
      useDesignerStore.temporal.getState().clear();
    };
  }, []);

  const updateNodeData = useCallback((nodeId: string, data: Record<string, unknown>) => {
    useDesignerStore.getState().updateNodeData(nodeId, data);
  }, []);

  const deleteNode = useCallback((nodeId: string) => {
    useDesignerStore.getState().deleteNodes([nodeId]);
  }, []);

  const ensureSaved = useCallback(async (): Promise<WorkflowVersion> => {
    if (savedVersion && !dirty) return savedVersion;
    const { nodes, edges } = useDesignerStore.getState();
    const definition = normalizeForSave(toDefinition(nodes, edges));
    // Block save/publish only on hard errors (no trigger, dangling edge,
    // unattached boundary); surface warnings (unreachable, no end) but proceed.
    const problems = validateGraph(definition);
    if (hasErrors(problems)) {
      throw new Error(problems.filter((p) => p.severity === "error").map((p) => p.message).join(" "));
    }
    const warnings = problems.filter((p) => p.severity === "warning");
    if (warnings.length > 0) {
      toast.warning(warnings.map((p) => p.message).join(" "));
    }
    const version = await saveDraft(id, definition);
    setSavedVersion(version);
    setBaseVersion(version);
    // The saved graph is the new clean baseline.
    useDesignerStore.temporal.getState().clear();
    return version;
  }, [savedVersion, dirty, id]);

  const handleSave = async () => {
    setBusy(true);
    try {
      const v = await ensureSaved();
      toast.success(`Saved draft v${v.version_number}`);
    } catch (e: unknown) {
      toast.error(getApiErrorMessage(e, "Save failed"));
    } finally {
      setBusy(false);
    }
  };

  const handlePublish = async () => {
    setBusy(true);
    try {
      const v = await ensureSaved();
      await publishVersion(id, v.id);
      toast.success(`Published v${v.version_number}`);
      await load();
    } catch (e: unknown) {
      toast.error(getApiErrorMessage(e, "Publish failed"));
    } finally {
      setBusy(false);
    }
  };

  const handleTest = async (input: {
    operation: string;
    before: Record<string, unknown> | null;
    after: Record<string, unknown> | null;
  }) => {
    setTesting(true);
    try {
      const v = await ensureSaved();
      setTestResult(await testVersion(id, v.id, input));
    } catch (e: unknown) {
      toast.error(getApiErrorMessage(e, "Test failed"));
    } finally {
      setTesting(false);
    }
  };

  if (isLoading) {
    return <Skeleton className="h-[80vh] w-full" />;
  }

  const inspector = (
    <>
      <NodeInspector
        node={selected}
        nodes={allNodes}
        fields={entityFields}
        entities={entities}
        forms={forms}
        onChangeData={updateNodeData}
        onDelete={deleteNode}
      />
      <FormsPanel forms={forms} entities={entities} />
      <TestPanel running={testing} result={testResult} fields={entityFields} onRun={handleTest} />
      {workflow ? (
        <RunPanel
          workflowId={workflow.id}
          entitySlug={entitySlug}
          fields={entityFields}
          runPermission={workflow.run_permission}
          onPermissionSaved={(p) => setWorkflow({ ...workflow, run_permission: p })}
          canRun={baseVersion?.status === "published" || workflow.active_version_id != null}
        />
      ) : null}
    </>
  );

  return (
    <div className="flex min-h-[calc(100vh-7rem)] flex-col gap-3 lg:h-[calc(100vh-7rem)] lg:min-h-0">
      <div className="flex flex-wrap items-center gap-2">
        <Link href="/workflows" className="text-muted-foreground hover:text-foreground">
          <ArrowLeft className="h-5 w-5" />
        </Link>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-semibold">{workflow?.name ?? "Workflow"}</h1>
            {entityName ? (
              <Badge variant="secondary" title="The entity whose record changes fire this workflow (fixed at creation)">
                Fires on {entityName}
              </Badge>
            ) : null}
          </div>
          <p className="text-xs text-muted-foreground">
            Drag from the palette to add a node · ⌘K for commands · drag a handle to connect.
          </p>
        </div>
        {errorCount > 0 ? (
          <Badge variant="destructive" title="Errors block save and publish">
            {errorCount} error{errorCount > 1 ? "s" : ""}
          </Badge>
        ) : warningCount > 0 ? (
          <Badge variant="outline" title="Warnings don't block save">
            {warningCount} warning{warningCount > 1 ? "s" : ""}
          </Badge>
        ) : null}
        {baseVersion ? (
          <Badge variant="outline">
            v{baseVersion.version_number} · {baseVersion.status}
          </Badge>
        ) : (
          <Badge variant="outline">unsaved</Badge>
        )}
        {dirty ? <Badge variant="outline">unsaved changes</Badge> : null}
        <Button variant="outline" size="sm" onClick={() => void handleSave()} disabled={busy}>
          <Save className="h-4 w-4" />
          Save draft
        </Button>
        <Button size="sm" onClick={() => void handlePublish()} disabled={busy}>
          <Rocket className="h-4 w-4" />
          Publish
        </Button>
      </div>

      {error ? <p className="text-sm text-destructive">{error}</p> : null}

      <div className="min-h-0 flex-1">
        <WorkflowDesigner inspector={inspector} onIssuesChange={setIssues} />
      </div>
    </div>
  );
}
