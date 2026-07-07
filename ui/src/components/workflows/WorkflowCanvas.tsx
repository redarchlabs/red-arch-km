"use client";

import { Background, Controls, MiniMap, ReactFlow } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useMemo } from "react";

import { LabeledEdge } from "@/components/workflows/designer/LabeledEdge";
import { isValidConnection } from "@/components/workflows/designer/isValidConnection";
import { useDesignerStore } from "@/components/workflows/designer/store";
import { useDragAndDrop } from "@/components/workflows/designer/useDragAndDrop";
import { ActionNode } from "@/components/workflows/nodes/ActionNode";
import { BoundaryEventNode } from "@/components/workflows/nodes/BoundaryEventNode";
import { ConditionNode } from "@/components/workflows/nodes/ConditionNode";
import { DelayNode } from "@/components/workflows/nodes/DelayNode";
import { EventNode } from "@/components/workflows/nodes/EventNode";
import { GatewayNode } from "@/components/workflows/nodes/GatewayNode";
import { GenericNode } from "@/components/workflows/nodes/GenericNode";
import { SwitchNode } from "@/components/workflows/nodes/SwitchNode";
import { TaskNode } from "@/components/workflows/nodes/TaskNode";
import { TriggerNode } from "@/components/workflows/nodes/TriggerNode";

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
  const { onDragOver, onDrop } = useDragAndDrop();

  const nodeTypes = useMemo(
    () => ({
      trigger: TriggerNode,
      task: TaskNode,
      gateway: GatewayNode,
      event: EventNode,
      boundaryEvent: BoundaryEventNode,
      action: ActionNode,
      condition: ConditionNode,
      switch: SwitchNode,
      delay: DelayNode,
      merge: GenericNode,
      passthrough: GenericNode,
    }),
    [],
  );
  const edgeTypes = useMemo(() => ({ labeled: LabeledEdge }), []);
  const validConnection = useMemo(() => isValidConnection(nodes), [nodes]);

  return (
    <div className="h-full w-full rounded-lg border bg-muted/20" onDragOver={onDragOver} onDrop={onDrop}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
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
        {/* MiniMap crowds a small touch canvas — desktop only. */}
        <MiniMap pannable zoomable className="!bg-background hidden lg:block" />
      </ReactFlow>
    </div>
  );
}
