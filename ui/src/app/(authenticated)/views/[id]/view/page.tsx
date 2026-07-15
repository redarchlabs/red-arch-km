"use client";

import { ArrowLeft } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
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
  // An entity-bound view can target a specific record via `?record_id=` — its
  // fields prefill, and run_workflow buttons run against that record (so an
  // update_record/update_record_field step writes it).
  const recordId = useSearchParams().get("record_id") ?? undefined;
  const router = useRouter();
  const [render, setRender] = useState<FormRender | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setRender(await getViewRender(id, recordId));
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to load view"));
    } finally {
      setLoading(false);
    }
  }, [id, recordId]);

  useEffect(() => {
    void load();
  }, [load]);

  // Return to wherever the user came from — the dashboard/view whose button
  // opened this one, or the view editor if they launched the runtime viewer
  // from there. (Previously this hard-linked to `/views/${id}`, which always
  // dumped end users into this view's *editor*.) Fall back to Home for a direct
  // deep-link with no in-app history to go back to.
  const handleBack = useCallback(() => {
    if (window.history.length > 1) router.back();
    else router.push("/home");
  }, [router]);

  const handleRunWorkflow = async (
    workflowId: string,
    inputs: Record<string, unknown>,
    rowRecordId?: string,
  ) => {
    setNotice(null);
    setError(null);
    try {
      // Target the row's record (record-list action), else the page's record
      // (`?record_id=`), else no record — an ad-hoc "run now". The run endpoint
      // needs a CRUD operation ("update" is the default); button `inputs` ride
      // along as `after` for workflows that reference them.
      // `record_id=me` is a RENDER-time sentinel (the server binds the view to the
      // caller's own record by email); the run endpoint only accepts a UUID, so we
      // never forward the sentinel. Instead we use the id the render RESOLVED it to
      // (`render.record_id`) so an entity-triggered button on a "me" view still runs
      // against the caller's own record; a manual workflow ignores record_id anyway.
      const pageRecordId = recordId === "me" ? (render?.record_id ?? null) : recordId;
      const target = rowRecordId ?? pageRecordId ?? null;
      // Pass the button values as BOTH `after` (entity-triggered workflows that
      // reference after.*) and `inputs` (manual-trigger workflows whose declared
      // inputs read inputs.*). The backend routes to the right one by trigger
      // type and drops undeclared keys, so sending both is safe.
      await runWorkflow(workflowId, { operation: "update", record_id: target, after: inputs, inputs });
      setNotice("Workflow started.");
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Workflow failed to start"));
    }
  };

  if (loading) return <Skeleton className="h-96 w-full" />;
  if (!render) return <p className="text-sm text-destructive">{error ?? "View not found."}</p>;

  return (
    <div className="space-y-6">
      {/* No page title here: each view supplies its own heading in its element
          tree, so echoing the internal view name (e.g. "Course Player") above it
          is redundant. Keep only a back affordance. */}
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={handleBack}
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" /> Back
        </button>
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
