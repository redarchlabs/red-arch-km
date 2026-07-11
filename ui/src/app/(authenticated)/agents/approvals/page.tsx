"use client";

import { ArrowLeft, Bell, Check, X } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  approveApproval,
  denyApproval,
  listApprovals,
  listNotifications,
  resolveNotification,
  type Approval,
  type Notification,
} from "@/lib/api/agents";
import { getApiErrorMessage } from "@/lib/api/errors";

export default function AgentApprovalsPage() {
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const [a, n] = await Promise.all([listApprovals(), listNotifications(true)]);
      setApprovals(a);
      setNotifications(n);
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to load inbox"));
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const decide = async (id: string, approve: boolean) => {
    try {
      await (approve ? approveApproval(id) : denyApproval(id));
      await load();
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to record decision"));
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Link href="/agents" className="text-muted-foreground hover:text-foreground">
          <ArrowLeft className="h-5 w-5" />
        </Link>
        <div className="flex-1">
          <h1 className="text-2xl font-semibold">Approvals & escalations</h1>
          <p className="text-sm text-muted-foreground">
            Approve or deny the actions agents paused on, and clear escalations they raised.
          </p>
        </div>
      </div>

      {error ? <p className="text-sm text-destructive">{error}</p> : null}

      <section className="space-y-2">
        <h2 className="text-sm font-medium">Pending approvals</h2>
        {isLoading ? (
          <Skeleton className="h-20 w-full" />
        ) : approvals.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nothing waiting on you.</p>
        ) : (
          approvals.map((a) => (
            <Card key={a.id}>
              <CardContent className="flex items-center gap-3 pt-6">
                <div className="flex-1">
                  <div className="font-mono text-sm font-medium">{a.tool_name}</div>
                  <pre className="mt-1 overflow-x-auto whitespace-pre-wrap text-xs text-muted-foreground">
                    {JSON.stringify(a.arguments, null, 2)}
                  </pre>
                </div>
                <Button size="sm" onClick={() => decide(a.id, true)}>
                  <Check className="h-4 w-4" /> Approve
                </Button>
                <Button size="sm" variant="outline" onClick={() => decide(a.id, false)}>
                  <X className="h-4 w-4" /> Deny
                </Button>
              </CardContent>
            </Card>
          ))
        )}
      </section>

      <section className="space-y-2">
        <h2 className="flex items-center gap-2 text-sm font-medium">
          <Bell className="h-4 w-4" /> Escalations & reviews
        </h2>
        {isLoading ? (
          <Skeleton className="h-16 w-full" />
        ) : notifications.length === 0 ? (
          <p className="text-sm text-muted-foreground">No open escalations.</p>
        ) : (
          notifications.map((n) => (
            <Card key={n.id}>
              <CardContent className="flex items-start gap-3 pt-6">
                <div className="flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium">{n.title}</span>
                    <Badge variant="outline">{n.kind}</Badge>
                  </div>
                  {n.body ? <p className="mt-1 text-sm text-muted-foreground">{n.body}</p> : null}
                </div>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={async () => {
                    await resolveNotification(n.id);
                    await load();
                  }}
                >
                  Resolve
                </Button>
              </CardContent>
            </Card>
          ))
        )}
      </section>
    </div>
  );
}
