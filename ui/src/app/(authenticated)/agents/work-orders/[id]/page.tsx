"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { getApiErrorMessage } from "@/lib/api/errors";
import {
  getWorkOrder,
  setWorkOrderStatus,
  type WorkOrderDetail,
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

export default function WorkOrderDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [wo, setWo] = useState<WorkOrderDetail | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setIsLoading(true);
    try {
      setWo(await getWorkOrder(id));
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

      {wo.body ? <p className="whitespace-pre-wrap text-sm">{wo.body}</p> : null}

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
        <CardContent className="space-y-2 pt-6">
          <h2 className="text-sm font-medium">Diary</h2>
          {wo.entries.length === 0 ? (
            <p className="text-sm text-muted-foreground">No activity yet.</p>
          ) : (
            wo.entries.map((e) => (
              <div key={e.id} className="border-l-2 pl-3 text-sm">
                <span className="text-xs text-muted-foreground">{e.role ?? "system"}: </span>
                {e.text}
              </div>
            ))
          )}
        </CardContent>
      </Card>
    </div>
  );
}
