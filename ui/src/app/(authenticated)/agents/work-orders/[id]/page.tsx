"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { Markdown } from "@/components/common/Markdown";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { AgentSwimLanes } from "@/components/workOrders/AgentSwimLanes";
import { getApiErrorMessage } from "@/lib/api/errors";
import {
  getWorkOrder,
  getWorkOrderMap,
  setWorkOrderStatus,
  type WorkOrderDetail,
  type WorkOrderMap,
  type WorkOrderStatus,
} from "@/lib/api/workOrders";

const NEXT_STATUS: Record<string, WorkOrderStatus[]> = {
  draft: ["approved", "in_progress", "cancelled"],
  awaiting_approval: ["approved", "cancelled"],
  approved: ["in_progress", "cancelled"],
  in_progress: ["done", "cancelled"],
  done: [],
  cancelled: [],
};

/** The agent's emoji, reused from the map so a diary entry and its lane carry the
 *  same icon. Falls back to a generic bot rather than nothing, so every entry
 *  still gets an avatar column and the rows stay aligned. */
function laneAvatar(map: WorkOrderMap | null, agentId: string | null): string {
  if (!agentId) return "🧑";
  return map?.lanes.find((lane) => lane.key === agentId)?.avatar ?? "🤖";
}

export default function WorkOrderDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [wo, setWo] = useState<WorkOrderDetail | null>(null);
  const [map, setMap] = useState<WorkOrderMap | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setIsLoading(true);
    try {
      // The map is supplementary: a failure there must not blank the page, so it
      // settles independently of the detail it decorates.
      const [detail, graph] = await Promise.allSettled([getWorkOrder(id), getWorkOrderMap(id)]);
      if (detail.status === "rejected") throw detail.reason;
      setWo(detail.value);
      setMap(graph.status === "fulfilled" ? graph.value : null);
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to load work order"));
    } finally {
      setIsLoading(false);
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  const move = async (status: WorkOrderStatus) => {
    try {
      await setWorkOrderStatus(id, status);
      await load();
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to change status"));
    }
  };

  if (isLoading) return <Skeleton className="h-64 w-full" />;
  if (!wo) return <p className="text-sm text-destructive">{error ?? "Not found"}</p>;

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Link href="/agents/work-orders" className="text-muted-foreground hover:text-foreground">
          <ArrowLeft className="h-5 w-5" />
        </Link>
        <div className="flex-1">
          <h1 className="text-2xl font-semibold">{wo.title}</h1>
          <p className="text-xs text-muted-foreground">{wo.slug}</p>
        </div>
        <Badge variant="outline">{wo.status}</Badge>
      </div>

      {error ? <p className="text-sm text-destructive">{error}</p> : null}

      <div className="flex flex-wrap gap-2">
        {(NEXT_STATUS[wo.status] ?? []).map((s) => (
          <Button key={s} size="sm" variant="outline" onClick={() => move(s)}>
            Move to {s}
          </Button>
        ))}
      </div>

      {wo.body ? <Markdown content={wo.body} className="text-sm" /> : null}

      {map && map.lanes.length > 0 ? (
        <Card>
          <CardContent className="space-y-3 pt-6">
            <h2 className="text-sm font-medium">Agent interactions</h2>
            <AgentSwimLanes map={map} />
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardContent className="space-y-2 pt-6">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-medium">Tasks</h2>
            <span className="text-xs text-muted-foreground">{Math.round(wo.progress * 100)}% complete</span>
          </div>
          {wo.tasks.length === 0 ? (
            <p className="text-sm text-muted-foreground">No tasks yet.</p>
          ) : (
            wo.tasks.map((t) => (
              <div key={t.id} className="flex items-center gap-2 text-sm">
                <Badge variant="outline">{t.key}</Badge>
                <span className="flex-1">{t.title}</span>
                <Badge variant="outline">{t.status}</Badge>
              </div>
            ))
          )}
        </CardContent>
      </Card>

      <Card>
        <CardContent className="space-y-3 pt-6">
          <h2 className="text-sm font-medium">Diary</h2>
          {wo.entries.length === 0 ? (
            <p className="text-sm text-muted-foreground">No activity yet.</p>
          ) : (
            <div className="space-y-3">
              {wo.entries.map((e) => (
                // Each entry is its own bounded block. Agent answers run to
                // hundreds of words, so entries separated only by a rule ran
                // together into one wall of text with no visible boundary
                // between who said what.
                <div key={e.id} className="rounded-md border bg-muted/20 p-3">
                  <div className="mb-1 flex items-center gap-2">
                    <span className="text-base leading-none">{laneAvatar(map, e.agent_id)}</span>
                    <span className="text-xs font-medium">{e.role ?? "system"}</span>
                    <span className="ml-auto text-[10px] text-muted-foreground">
                      {new Date(e.created_at).toLocaleString()}
                    </span>
                  </div>
                  {/* Diary text is written by agents, so it arrives as Markdown and
                      has to be rendered as such — a wall of literal ** is unreadable.
                      Images are stripped: a model talked into emitting one (via a
                      poisoned document) would make the reader's browser fetch an
                      attacker URL. */}
                  <Markdown content={e.text} stripImages className="text-sm" />
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
