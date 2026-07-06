"use client";

import {
  Background,
  Controls,
  MiniMap,
  Panel,
  ReactFlow,
  type Edge,
  type Node,
  type OnConnect,
  type OnEdgesChange,
  type OnNodesChange,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { GitBranch, Bolt } from "lucide-react";
import { useMemo } from "react";

import { Button } from "@/components/ui/button";
import { ActionNode } from "@/components/workflows/nodes/ActionNode";
import { ConditionNode } from "@/components/workflows/nodes/ConditionNode";
import { TriggerNode } from "@/components/workflows/nodes/TriggerNode";

interface WorkflowCanvasProps {
  nodes: Node[];
  edges: Edge[];
  onNodesChange?: OnNodesChange;
  onEdgesChange?: OnEdgesChange;
  onConnect?: OnConnect;
  onNodeClick?: (node: Node) => void;
  onAddNode?: (type: "condition" | "action") => void;
  readOnly?: boolean;
}

export function WorkflowCanvas({
  nodes,
  edges,
  onNodesChange,
  onEdgesChange,
  onConnect,
  onNodeClick,
  onAddNode,
  readOnly = false,
}: WorkflowCanvasProps) {
  const nodeTypes = useMemo(
    () => ({ trigger: TriggerNode, condition: ConditionNode, action: ActionNode }),
    [],
  );

  return (
    <div className="h-full w-full rounded-lg border bg-muted/20">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onNodeClick={(_, node) => onNodeClick?.(node)}
        nodesDraggable={!readOnly}
        nodesConnectable={!readOnly}
        elementsSelectable
        fitView
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={16} />
        <Controls showInteractive={false} />
        <MiniMap pannable zoomable className="!bg-background" />
        {onAddNode && !readOnly ? (
          <Panel position="top-left" className="flex gap-2">
            <Button size="sm" variant="secondary" onClick={() => onAddNode("condition")}>
              <GitBranch className="h-4 w-4" />
              Condition
            </Button>
            <Button size="sm" variant="secondary" onClick={() => onAddNode("action")}>
              <Bolt className="h-4 w-4" />
              Action
            </Button>
          </Panel>
        ) : null}
      </ReactFlow>
    </div>
  );
}
