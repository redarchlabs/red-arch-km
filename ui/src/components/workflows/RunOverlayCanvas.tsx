"use client";

import { Background, Controls, ReactFlow, ReactFlowProvider } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useMemo } from "react";

import { NodeChromeContext } from "@/components/workflows/nodes/BaseNode";
import { EDGE_TYPES, NODE_TYPES } from "@/components/workflows/nodeTypes";
import { toReactFlow } from "@/components/workflows/graphSerde";
import { useRunStream } from "@/components/workflows/useRunStream";
import type { WorkflowDefinition } from "@/lib/api/workflows";

const LEGEND: { label: string; className: string }[] = [
  { label: "Running", className: "bg-sky-400" },
  { label: "Waiting", className: "bg-amber-400" },
  { label: "Done", className: "bg-emerald-400" },
  { label: "Failed", className: "bg-rose-500" },
];

/**
 * Read-only canvas that overlays a run's live state on its workflow graph: each
 * node is colored by status (via the shared status-ring in {@link NodeChromeContext})
 * as tokens advance. Fed by {@link useRunStream} (SSE, polling fallback). Render
 * anywhere — it provides its own ReactFlowProvider.
 */
export function RunOverlayCanvas({
  definition,
  runId,
  active = true,
}: {
  definition: WorkflowDefinition;
  runId: string;
  active?: boolean;
}) {
  const { chrome, runStatus, live } = useRunStream(runId, active);
  const { nodes, edges } = useMemo(() => toReactFlow(definition), [definition]);

  return (
    <div className="flex h-full w-full flex-col gap-2">
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <div className="flex items-center gap-3">
          {LEGEND.map((item) => (
            <span key={item.label} className="flex items-center gap-1">
              <span className={`inline-block h-2.5 w-2.5 rounded-full ${item.className}`} />
              {item.label}
            </span>
          ))}
        </div>
        <span className="flex items-center gap-1.5">
          <span
            className={`inline-block h-2 w-2 rounded-full ${live ? "bg-emerald-500 animate-pulse" : "bg-muted-foreground/40"}`}
            title={live ? "Live (streaming)" : "Polling"}
          />
          {runStatus ? <span className="capitalize">{runStatus}</span> : null}
        </span>
      </div>
      <div className="min-h-0 flex-1 rounded-lg border bg-muted/20">
        <ReactFlowProvider>
          <NodeChromeContext.Provider value={chrome}>
            <ReactFlow
              nodes={nodes}
              edges={edges}
              nodeTypes={NODE_TYPES}
              edgeTypes={EDGE_TYPES}
              defaultEdgeOptions={{ type: "labeled" }}
              nodesDraggable={false}
              nodesConnectable={false}
              elementsSelectable={false}
              deleteKeyCode={null}
              fitView
              proOptions={{ hideAttribution: true }}
            >
              <Background gap={16} />
              <Controls showInteractive={false} />
            </ReactFlow>
          </NodeChromeContext.Provider>
        </ReactFlowProvider>
      </div>
    </div>
  );
}
