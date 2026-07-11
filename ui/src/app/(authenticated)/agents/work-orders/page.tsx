"use client";

import { ArrowLeft, ClipboardList, Plus } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { listAgents, type Agent } from "@/lib/api/agents";
import { getApiErrorMessage } from "@/lib/api/errors";
import { createWorkOrder, listWorkOrders, type WorkOrder } from "@/lib/api/workOrders";

const selectClass = "h-9 w-full rounded-md border bg-background px-2 text-sm";

export default function WorkOrdersPage() {
  const [items, setItems] = useState<WorkOrder[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filing, setFiling] = useState(false);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [assignee, setAssignee] = useState("");
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const [w, a] = await Promise.all([listWorkOrders(), listAgents()]);
      setItems(w);
      setAgents(a);
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to load work orders"));
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const file = async () => {
    setSaving(true);
    try {
      await createWorkOrder({ title, body: body || null, assigned_agent_id: assignee || null });
      setFiling(false);
      setTitle("");
      setBody("");
      setAssignee("");
      await load();
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to file work order"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Link href="/agents" className="text-muted-foreground hover:text-foreground">
          <ArrowLeft className="h-5 w-5" />
        </Link>
        <div className="flex-1">
          <h1 className="flex items-center gap-2 text-2xl font-semibold">
            <ClipboardList className="h-6 w-6" /> Work orders
          </h1>
          <p className="text-sm text-muted-foreground">
            File a work order and assign a supervisor agent to decompose and delegate it.
          </p>
        </div>
        <Button size="sm" onClick={() => setFiling(true)}>
          <Plus className="h-4 w-4" /> File work order
        </Button>
      </div>

      {error ? <p className="text-sm text-destructive">{error}</p> : null}

      {isLoading ? (
        <Skeleton className="h-40 w-full" />
      ) : items.length === 0 ? (
        <p className="text-sm text-muted-foreground">No work orders yet.</p>
      ) : (
        <div className="space-y-2">
          {items.map((wo) => (
            <Card key={wo.id}>
              <CardContent className="flex items-center gap-3 pt-6">
                <div className="flex-1">
                  <div className="flex items-center gap-2">
                    <span className="font-medium">{wo.title}</span>
                    <Badge variant="outline">{wo.status}</Badge>
                    <Badge variant="outline">{wo.priority}</Badge>
                  </div>
                  <p className="text-xs text-muted-foreground">{wo.slug}</p>
                </div>
                <Link href={`/agents/work-orders/${wo.id}`}>
                  <Button size="sm" variant="outline">
                    Open
                  </Button>
                </Link>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {filing ? (
        <Dialog open onClose={() => setFiling(false)}>
          <DialogHeader>
            <DialogTitle>File a work order</DialogTitle>
          </DialogHeader>
          <div className="space-y-3 px-1 py-2">
            <label className="block text-sm">
              Title
              <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Onboard new hire" />
            </label>
            <label className="block text-sm">
              Details
              <textarea
                className="mt-1 h-24 w-full rounded-md border bg-background px-2 py-1 text-sm"
                value={body}
                onChange={(e) => setBody(e.target.value)}
              />
            </label>
            <label className="block text-sm">
              Assign to supervisor agent
              <select className={selectClass} value={assignee} onChange={(e) => setAssignee(e.target.value)}>
                <option value="">— unassigned —</option>
                {agents.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setFiling(false)} disabled={saving}>
              Cancel
            </Button>
            <Button onClick={file} disabled={saving || !title}>
              {saving ? "Filing…" : "File"}
            </Button>
          </DialogFooter>
        </Dialog>
      ) : null}
    </div>
  );
}
