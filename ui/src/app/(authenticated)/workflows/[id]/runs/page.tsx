"use client";

import { ArrowLeft, Pencil } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { RunMonitor } from "@/components/workflows/RunMonitor";
import { getApiErrorMessage } from "@/lib/api/errors";
import { getWorkflow, type Workflow } from "@/lib/api/workflows";

export default function WorkflowRunsPage() {
  const { id } = useParams<{ id: string }>();
  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Read ?run=<id> from the URL (the activity feed deep-links here) via
  // window.location so no Suspense boundary is needed for useSearchParams.
  const [initialRunId, setInitialRunId] = useState<string | null>(null);

  useEffect(() => {
    setInitialRunId(new URLSearchParams(window.location.search).get("run"));
  }, []);

  useEffect(() => {
    getWorkflow(id)
      .then(setWorkflow)
      .catch((e: unknown) => setError(getApiErrorMessage(e, "Failed to load workflow")));
  }, [id]);

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Link href="/workflows" className="text-muted-foreground hover:text-foreground">
          <ArrowLeft className="h-5 w-5" />
        </Link>
        <div className="flex-1">
          <h1 className="text-2xl font-semibold">{workflow?.name ?? "Workflow"} · Runs</h1>
          <p className="text-sm text-muted-foreground">Execution history, statuses, and retries.</p>
        </div>
        <Link href={`/workflows/${id}/design`}>
          <Button variant="outline" size="sm">
            <Pencil className="h-4 w-4" />
            Design
          </Button>
        </Link>
      </div>

      {error ? <p className="text-sm text-destructive">{error}</p> : null}

      <RunMonitor workflowId={id} initialRunId={initialRunId} />
    </div>
  );
}
