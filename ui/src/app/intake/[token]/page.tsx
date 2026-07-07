"use client";

import { use, useCallback, useEffect, useState } from "react";

import { getPublicForm, submitPublicForm, type FormRender, type FormSubmit } from "@/lib/api/forms";
import { FormRenderer } from "@/components/forms/FormRenderer";

/**
 * Public, unauthenticated intake page. The external user arrives with only a
 * token; the API resolves the org from it. No app chrome, no auth. Rendering +
 * submission are handled by the shared `<FormRenderer>` (the same component the
 * authenticated fill page and builder preview use). Workflow-run buttons are not
 * wired here — the public path is unauthenticated by design.
 */
export default function IntakeFormPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = use(params);

  const [form, setForm] = useState<FormRender | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      setForm(await getPublicForm(token));
    } catch (e: unknown) {
      setLoadError(e instanceof Error ? e.message : "Unable to load this form.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleSubmit = async (payload: FormSubmit) => {
    setSubmitting(true);
    setSubmitError(null);
    try {
      await submitPublicForm(token, payload);
      setDone(true);
    } catch (e: unknown) {
      setSubmitError(e instanceof Error ? e.message : "Submission failed.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col justify-center px-4 py-10">
      {loading ? (
        <p className="text-center text-muted-foreground">Loading…</p>
      ) : loadError ? (
        <Notice title="This form isn't available" body={loadError} />
      ) : done ? (
        <Notice title="Thank you" body="Your response has been submitted." tone="success" />
      ) : form && form.status !== "pending" && form.status !== "editable" ? (
        <Notice
          title={form.status === "submitted" ? "Already submitted" : "Link expired"}
          body={
            form.status === "submitted"
              ? "This form has already been completed."
              : "This form link is no longer active. Please request a new one."
          }
        />
      ) : form ? (
        <div className="space-y-6 rounded-lg border bg-card p-6 shadow-sm">
          <header className="space-y-1">
            <h1 className="text-2xl font-semibold">{form.form_name}</h1>
            {form.description ? <p className="text-sm text-muted-foreground">{form.description}</p> : null}
          </header>
          <FormRenderer
            render={form}
            mode="fill"
            onSubmit={handleSubmit}
            submitting={submitting}
            error={submitError}
            defaultSubmitLabel="Submit"
          />
        </div>
      ) : null}
    </main>
  );
}

function Notice({
  title,
  body,
  tone = "neutral",
}: {
  title: string;
  body: string;
  tone?: "neutral" | "success";
}) {
  return (
    <div className="rounded-lg border bg-card p-8 text-center shadow-sm">
      <h1 className={`text-xl font-semibold ${tone === "success" ? "text-green-600" : ""}`}>{title}</h1>
      <p className="mt-2 text-sm text-muted-foreground">{body}</p>
    </div>
  );
}
