"use client";

import { RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { getApiErrorMessage } from "@/lib/api/errors";
import { fetchSystemStatus, type SystemStatus as SystemStatusData } from "@/lib/api/system";

const COMPONENT_LABELS: Record<string, string> = {
  database: "PostgreSQL",
  redis: "Redis",
  brain_api: "Brain API",
  worker_queue: "Worker queue",
};

export function SystemStatus() {
  const [status, setStatus] = useState<SystemStatusData | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      setStatus(await fetchSystemStatus());
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to load system status"));
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <Card>
      <CardContent className="space-y-4 pt-6">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold">System status</h2>
            <p className="text-sm text-muted-foreground">
              {status ? `API version ${status.version}` : "Platform component health."}
            </p>
          </div>
          <Button variant="outline" size="sm" onClick={() => void load()} disabled={isLoading}>
            <RefreshCw className="h-4 w-4" />
            Refresh
          </Button>
        </div>

        {error ? <p className="text-sm text-destructive">{error}</p> : null}

        {isLoading ? (
          <Skeleton className="h-32 w-full" />
        ) : status ? (
          <div className="grid gap-3 sm:grid-cols-2">
            {Object.entries(status.components).map(([key, component]) => (
              <div key={key} className="rounded-md border p-3">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium">{COMPONENT_LABELS[key] ?? key}</span>
                  <Badge variant={component.status === "ok" ? "default" : "destructive"}>
                    {component.status}
                  </Badge>
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                  {component.latency_ms != null ? `${component.latency_ms} ms` : null}
                  {component.latency_ms != null && component.detail ? " · " : null}
                  {component.detail}
                </p>
              </div>
            ))}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
