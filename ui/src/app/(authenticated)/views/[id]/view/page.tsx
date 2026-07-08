"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { use, useCallback, useEffect, useState } from "react";

import { FormRenderer } from "@/components/forms/FormRenderer";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { getApiErrorMessage } from "@/lib/api/errors";
import type { FormRender } from "@/lib/api/forms";
import { getViewRender } from "@/lib/api/views";
import { runWorkflow } from "@/lib/api/workflows";

/** Runtime viewer: renders a view through the shared `FormRenderer`. Buttons run
 * workflows or navigate; embedded forms render inline. */
export default function ViewViewerPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [render, setRender] = useState<FormRender | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setRender(await getViewRender(id));
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to load view"));
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleRunWorkflow = async (workflowId: string, inputs: Record<string, unknown>) => {
    setNotice(null);
    try {
      // A view button is an ad-hoc "run now" with no record context. The run
      // endpoint requires a real CRUD operation (it bypasses trigger matching,
      // so the value is cosmetic); "update" is the schema default. Button
      // `inputs` ride along as `after` for workflows that reference them.
      await runWorkflow(workflowId, { operation: "update", after: inputs });
      setNotice("Workflow started.");
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Workflow failed to start"));
    }
  };

  if (loading) return <Skeleton className="h-96 w-full" />;
  if (!render) return <p className="text-sm text-destructive">{error ?? "View not found."}</p>;

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Link href={`/views/${id}`} className="text-muted-foreground hover:text-foreground">
          <ArrowLeft className="h-5 w-5" />
        </Link>
        <h1 className="flex-1 text-2xl font-semibold">{render.form_name}</h1>
      </div>
      {error ? <p className="text-sm text-destructive">{error}</p> : null}
      {notice ? <p className="text-sm text-green-600">{notice}</p> : null}
      <Card>
        <CardContent className="pt-6">
          <FormRenderer render={render} mode="fill" onRunWorkflow={handleRunWorkflow} />
        </CardContent>
      </Card>
    </div>
  );
}
