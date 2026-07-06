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
import { GitBranch, Bolt, Clock, Split } from "lucide-react";
import { useMemo } from "react";

import { Button } from "@/components/ui/button";
import { ActionNode } from "@/components/workflows/nodes/ActionNode";
import { ConditionNode } from "@/components/workflows/nodes/ConditionNode";
import { DelayNode } from "@/components/workflows/nodes/DelayNode";
import { SwitchNode } from "@/components/workflows/nodes/SwitchNode";
import { TriggerNode } from "@/components/workflows/nodes/TriggerNode";

export type AddableNodeType = "condition" | "action" | "switch" | "delay";

interface WorkflowCanvasProps {
  nodes: Node[];
  edges: Edge[];
  onNodesChange?: OnNodesChange;
  onEdgesChange?: OnEdgesChange;
  onConnect?: OnConnect;
  onNodeClick?: (node: Node) => void;
  onAddNode?: (type: AddableNodeType) => void;
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
    () => ({
      trigger: TriggerNode,
      condition: ConditionNode,
      action: ActionNode,
      switch: SwitchNode,
      delay: DelayNode,
    }),
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
        {/* MiniMap crowds a small touch canvas — desktop only. */}
        <MiniMap pannable zoomable className="!bg-background hidden lg:block" />
        {onAddNode && !readOnly ? (
          <Panel position="top-left" className="flex max-w-[calc(100%-1rem)] flex-wrap gap-2">
            <Button size="sm" variant="secondary" onClick={() => onAddNode("condition")}>
              <GitBranch className="h-4 w-4" />
              Condition
            </Button>
            <Button size="sm" variant="secondary" onClick={() => onAddNode("switch")}>
              <Split className="h-4 w-4" />
              Switch
            </Button>
            <Button size="sm" variant="secondary" onClick={() => onAddNode("delay")}>
              <Clock className="h-4 w-4" />
              Delay
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
