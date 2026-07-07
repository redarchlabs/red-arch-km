"use client";

import {
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  type Connection,
  type Edge,
  type EdgeChange,
  type Node,
  type NodeChange,
} from "@xyflow/react";
import { ArrowLeft, Rocket, Save } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { FormsPanel } from "@/components/workflows/FormsPanel";
import { NodeInspector } from "@/components/workflows/NodeInspector";
import { RunPanel } from "@/components/workflows/RunPanel";
import { TestPanel } from "@/components/workflows/TestPanel";
import { WorkflowCanvas, type AddableNodeType } from "@/components/workflows/WorkflowCanvas";
import {
  checkGraphIntegrity,
  newNodeId,
  normalizeForSave,
  starterGraph,
  toDefinition,
  toReactFlow,
} from "@/components/workflows/graphSerde";
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
  const [nodes, setNodes] = useState<Node[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [baseVersion, setBaseVersion] = useState<WorkflowVersion | null>(null);
  const [savedVersion, setSavedVersion] = useState<WorkflowVersion | null>(null);
  const [dirty, setDirty] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [testResult, setTestResult] = useState<WorkflowTestResult | null>(null);
  const [testing, setTesting] = useState(false);

  const selected = useMemo(() => nodes.find((n) => n.selected) ?? null, [nodes]);

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
      setNodes(graph.nodes);
      setEdges(graph.edges);
      setBaseVersion(latest);
      // If the latest version is an unpublished draft, we keep editing it;
      // editing a published version forks a fresh draft on save.
      setSavedVersion(latest && latest.status === "draft" ? latest : null);
      setDirty(false);
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

  const markDirty = (changes: { type: string }[]) => {
    if (changes.some((c) => c.type !== "select" && c.type !== "dimensions")) setDirty(true);
  };

  const onNodesChange = useCallback((changes: NodeChange[]) => {
    setNodes((nds) => applyNodeChanges(changes, nds));
    markDirty(changes);
  }, []);

  const onEdgesChange = useCallback((changes: EdgeChange[]) => {
    setEdges((eds) => applyEdgeChanges(changes, eds));
    markDirty(changes);
  }, []);

  const onConnect = useCallback((connection: Connection) => {
    const edge: Edge = {
      ...connection,
      id: newNodeId("e"),
      style: connection.sourceHandle === "false" ? { stroke: "#f43f5e" } : undefined,
    };
    setEdges((eds) => addEdge(edge, eds));
    setDirty(true);
  }, []);

  const addNode = useCallback((type: AddableNodeType) => {
    const dataByType: Record<AddableNodeType, Record<string, unknown>> = {
      condition: { expr: null },
      action: { action_type: "", config: {} },
      switch: { cases: [] },
      delay: { delay_amount: 30, delay_unit: "minutes", delay_seconds: 1800 },
    };
    const node: Node = {
      id: newNodeId(type),
      type,
      position: { x: 200 + Math.random() * 120, y: 200 + Math.random() * 160 },
      data: dataByType[type],
    };
    setNodes((nds) => [...nds, node]);
    setDirty(true);
  }, []);

  const updateNodeData = useCallback((nodeId: string, data: Record<string, unknown>) => {
    setNodes((nds) => nds.map((n) => (n.id === nodeId ? { ...n, data } : n)));
    setDirty(true);
  }, []);

  const deleteNode = useCallback((nodeId: string) => {
    setNodes((nds) => nds.filter((n) => n.id !== nodeId));
    setEdges((eds) => eds.filter((e) => e.source !== nodeId && e.target !== nodeId));
    setDirty(true);
  }, []);

  const ensureSaved = useCallback(async (): Promise<WorkflowVersion> => {
    if (savedVersion && !dirty) return savedVersion;
    const definition = normalizeForSave(toDefinition(nodes, edges));
    // Block structurally broken graphs (cycles, edges to deleted nodes); warn
    // about unreachable nodes but let the save proceed.
    const { errors, warnings } = checkGraphIntegrity(definition);
    if (errors.length > 0) {
      throw new Error(errors.join(" "));
    }
    if (warnings.length > 0) {
      toast.warning(warnings.join(" "));
    }
    const version = await saveDraft(id, definition);
    setSavedVersion(version);
    setBaseVersion(version);
    setDirty(false);
    return version;
  }, [savedVersion, dirty, nodes, edges, id]);

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
            Drag from a node handle to connect. Green = true branch, red = false.
          </p>
        </div>
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

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 lg:grid-cols-[1fr_360px]">
        <div className="h-[60vh] min-h-[360px] lg:h-auto">
          <WorkflowCanvas
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onAddNode={addNode}
          />
        </div>
        <div className="min-h-0 space-y-3 overflow-y-auto">
          <NodeInspector
            node={selected}
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
        </div>
      </div>
    </div>
  );
}
