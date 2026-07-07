"use client";

import { useEffect, useState } from "react";

import { getFormRender, type FormRender } from "@/lib/api/forms";
import { FormRenderer } from "./FormRenderer";

/**
 * Read-only preview of a form's element tree, rendered by the shared
 * `<FormRenderer>` in preview mode. Pass a prebuilt `render` (e.g. the builder's
 * live, unsaved layout resolved against the loaded entities) or a `formId` to
 * fetch the resolved schema from the server.
 */
interface FormPreviewProps {
  render?: FormRender;
  formId?: string;
}

export function FormPreview({ render, formId }: FormPreviewProps) {
  const [fetched, setFetched] = useState<FormRender | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (render || !formId) return;
    let active = true;
    getFormRender(formId)
      .then((r) => active && setFetched(r))
      .catch((e: unknown) => active && setError(e instanceof Error ? e.message : "Preview unavailable"));
    return () => {
      active = false;
    };
  }, [render, formId]);

  const data = render ?? fetched;
  if (error) return <p className="text-sm text-destructive">{error}</p>;
  if (!data) return <p className="text-sm text-muted-foreground">Loading preview…</p>;

  return (
    <div className="rounded-lg border bg-card p-4">
      {data.form_name ? <h3 className="mb-3 text-lg font-semibold">{data.form_name}</h3> : null}
      <FormRenderer render={data} mode="preview" />
    </div>
  );
}
