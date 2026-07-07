"use client";

/**
 * The designer shell: a 3-pane workspace (palette · canvas · inspector) wrapped
 * in a `<ReactFlowProvider>`. It owns the interaction layer — keymap, ⌘K command
 * palette, drop-to-place — and derives per-node validation chrome (problem
 * badges) from the store, exposing the full issue list to the host page.
 */
import { ReactFlowProvider } from "@xyflow/react";
import { useEffect, useMemo, type ReactNode } from "react";

import { toDefinition } from "@/components/workflows/graphSerde";
import { NodeChromeContext, type NodeChrome } from "@/components/workflows/nodes/BaseNode";
import { validateGraph, type Issue } from "@/components/workflows/validation";

import { CommandPalette } from "./CommandPalette";
import { NodePalette } from "./NodePalette";
import { useDesignerKeymap } from "./keymap";
import { useDesignerStore } from "./store";
import { WorkflowCanvas } from "@/components/workflows/WorkflowCanvas";

interface WorkflowDesignerProps {
  /** Right-pane content (the host supplies its inspector + side panels). */
  inspector: ReactNode;
  readOnly?: boolean;
  /** Receives the live validation issues so the host can gate save/publish. */
  onIssuesChange?: (issues: Issue[]) => void;
}

function chromeFromIssues(issues: Issue[]): Record<string, NodeChrome> {
  const map: Record<string, NodeChrome> = {};
  for (const issue of issues) {
    if (!issue.nodeId) continue;
    const current = map[issue.nodeId];
    const severity = current?.problem === "error" || issue.severity === "error" ? "error" : "warning";
    map[issue.nodeId] = { problem: severity, problemCount: (current?.problemCount ?? 0) + 1 };
  }
  return map;
}

export function WorkflowDesigner({ inspector, readOnly = false, onIssuesChange }: WorkflowDesignerProps) {
  const nodes = useDesignerStore((s) => s.nodes);
  const edges = useDesignerStore((s) => s.edges);

  useDesignerKeymap({ disabled: readOnly });

  const issues = useMemo<Issue[]>(() => {
    try {
      return validateGraph(toDefinition(nodes, edges));
    } catch {
      // toDefinition throws only on an unknown node type; treat as no chrome.
      return [];
    }
  }, [nodes, edges]);

  const chrome = useMemo(() => chromeFromIssues(issues), [issues]);

  useEffect(() => {
    onIssuesChange?.(issues);
  }, [issues, onIssuesChange]);

  return (
    <ReactFlowProvider>
      <NodeChromeContext.Provider value={chrome}>
        <div className="grid h-full min-h-0 grid-cols-1 gap-3 lg:grid-cols-[210px_1fr_360px]">
          <NodePalette className="hidden max-h-full lg:block" />
          <div className="h-[60vh] min-h-[360px] lg:h-auto">
            <WorkflowCanvas readOnly={readOnly} />
          </div>
          <div className="min-h-0 space-y-3 overflow-y-auto">{inspector}</div>
        </div>
        <CommandPalette />
      </NodeChromeContext.Provider>
    </ReactFlowProvider>
  );
}
