"use client";

import { Background, Controls, MiniMap, Panel, ReactFlow, useReactFlow } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Network } from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { layoutGraph } from "@/components/workflows/designer/autoLayout";
import { isValidConnection } from "@/components/workflows/designer/isValidConnection";
import { useDesignerStore } from "@/components/workflows/designer/store";
import { useDragAndDrop } from "@/components/workflows/designer/useDragAndDrop";
import { EDGE_TYPES, NODE_TYPES } from "@/components/workflows/nodeTypes";

interface WorkflowCanvasProps {
  readOnly?: boolean;
}

/**
 * The React Flow surface, driven entirely by the designer store. Node/edge
 * types come from the registry; connections are validated live; palette drops
 * land where released. Must be rendered inside a `<ReactFlowProvider>`. Delete
 * is disabled here and handled by the keymap so boundary children cascade.
 */
export function WorkflowCanvas({ readOnly = false }: WorkflowCanvasProps) {
  const nodes = useDesignerStore((s) => s.nodes);
  const edges = useDesignerStore((s) => s.edges);
  const onNodesChange = useDesignerStore((s) => s.onNodesChange);
  const onEdgesChange = useDesignerStore((s) => s.onEdgesChange);
  const onConnect = useDesignerStore((s) => s.onConnect);
  const applyLayout = useDesignerStore((s) => s.applyLayout);
  const { onDragOver, onDrop } = useDragAndDrop();
  const { fitView } = useReactFlow();
  const [layingOut, setLayingOut] = useState(false);

  const handleAutoLayout = useCallback(async () => {
    setLayingOut(true);
    try {
      // Read fresh from the store so a mid-edit layout uses the latest graph.
      const { nodes: current, edges: currentEdges } = useDesignerStore.getState();
      const laidOut = await layoutGraph(current, currentEdges);
      applyLayout(laidOut);
      // Re-fit on the next frame, once React Flow has the new positions.
      requestAnimationFrame(() => void fitView({ padding: 0.2, duration: 300 }));
    } catch {
      toast.error("Auto-layout failed. Try again or arrange the nodes manually.");
    } finally {
      setLayingOut(false);
    }
  }, [applyLayout, fitView]);

  const validConnection = useMemo(() => isValidConnection(nodes), [nodes]);

  return (
    <div className="h-full w-full rounded-lg border bg-muted/20" onDragOver={onDragOver} onDrop={onDrop}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        edgeTypes={EDGE_TYPES}
        onNodesChange={readOnly ? undefined : onNodesChange}
        onEdgesChange={readOnly ? undefined : onEdgesChange}
        onConnect={readOnly ? undefined : onConnect}
        isValidConnection={validConnection}
        defaultEdgeOptions={{ type: "labeled" }}
        deleteKeyCode={null}
        nodesDraggable={!readOnly}
        nodesConnectable={!readOnly}
        elementsSelectable
        fitView
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={16} />
        <Controls showInteractive={false} />
        {readOnly ? null : (
          <Panel position="top-right">
            <Button
              variant="outline"
              size="sm"
              onClick={() => void handleAutoLayout()}
              disabled={layingOut || nodes.length === 0}
              className="bg-background shadow-sm"
              title="Arrange the nodes top-to-bottom with a layered auto-layout"
            >
              <Network className="h-4 w-4" />
              {layingOut ? "Laying out…" : "Auto-layout"}
            </Button>
          </Panel>
        )}
        {/* MiniMap crowds a small touch canvas — desktop only. */}
        <MiniMap pannable zoomable className="!bg-background hidden lg:block" />
      </ReactFlow>
    </div>
  );
}
