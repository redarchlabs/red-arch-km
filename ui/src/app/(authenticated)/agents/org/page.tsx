"use client";

import {
  Background,
  Controls,
  ReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { ArrowLeft, Network } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { listAgents, type Agent } from "@/lib/api/agents";
import { getApiErrorMessage } from "@/lib/api/errors";

const X_SPACING = 230;
const Y_SPACING = 130;

/** Depth = distance up the supervisor chain (cycle-guarded), so roots sit at top. */
function depthOf(agent: Agent, byId: Map<string, Agent>): number {
  const seen = new Set<string>();
  let depth = 0;
  let cursor: string | null = agent.supervisor_id;
  while (cursor && !seen.has(cursor)) {
    seen.add(cursor);
    depth += 1;
    cursor = byId.get(cursor)?.supervisor_id ?? null;
  }
  return depth;
}

function layout(agents: Agent[]): { nodes: Node[]; edges: Edge[] } {
  const byId = new Map(agents.map((a) => [a.id, a]));
  const perDepth = new Map<number, number>();
  const nodes: Node[] = agents.map((a) => {
    const depth = depthOf(a, byId);
    const col = perDepth.get(depth) ?? 0;
    perDepth.set(depth, col + 1);
    return {
      id: a.id,
      position: { x: col * X_SPACING, y: depth * Y_SPACING },
      data: { label: `${a.display_name ?? a.name}\n(${a.kind})` },
      style: {
        whiteSpace: "pre-line",
        textAlign: "center",
        fontSize: 12,
        borderRadius: 8,
        opacity: a.enabled ? 1 : 0.5,
      },
    };
  });
  const edges: Edge[] = agents
    .filter((a) => a.supervisor_id && byId.has(a.supervisor_id))
    .map((a) => ({ id: `${a.supervisor_id}-${a.id}`, source: a.supervisor_id as string, target: a.id }));
  return { nodes, edges };
}

export default function AgentOrgChartPage() {
  const router = useRouter();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setIsLoading(true);
    try {
      setAgents(await listAgents());
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to load agents"));
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const { nodes, edges } = useMemo(() => layout(agents), [agents]);

  return (
    <div className="flex h-[calc(100vh-8rem)] flex-col space-y-4">
      <div className="flex items-center gap-3">
        <Link href="/agents" className="text-muted-foreground hover:text-foreground">
          <ArrowLeft className="h-5 w-5" />
        </Link>
        <div className="flex-1">
          <h1 className="flex items-center gap-2 text-2xl font-semibold">
            <Network className="h-6 w-6" /> Org chart
          </h1>
          <p className="text-sm text-muted-foreground">
            The supervisor hierarchy. Click an agent to open its console; edit reporting lines in the
            agent editor.
          </p>
        </div>
      </div>

      {error ? <p className="text-sm text-destructive">{error}</p> : null}

      <div className="flex-1 overflow-hidden rounded-md border">
        {isLoading ? (
          <Skeleton className="h-full w-full" />
        ) : (
          <ReactFlow
            nodes={nodes}
            edges={edges}
            fitView
            nodesConnectable={false}
            onNodeClick={(_e, node) => router.push(`/agents/${node.id}/console`)}
          >
            <Background />
            <Controls showInteractive={false} />
          </ReactFlow>
        )}
      </div>
    </div>
  );
}
