"use client";

import { FileText, Loader2, Trash2, Upload } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { getApiErrorMessage } from "@/lib/api/errors";
import {
  attachWorkOrderDocuments,
  detachWorkOrderArtifact,
  listWorkOrderArtifacts,
  type WorkOrderArtifact,
} from "@/lib/api/workOrders";
import { uploadDocument } from "@/lib/api/documents";

/**
 * What went into a work order and what came out of it.
 *
 * The diary says what happened; this says what came of it — the audit report an
 * agent wrote, the spec somebody handed in. Inputs and outputs are labelled
 * because they answer different questions: an agent starting work wants the
 * inputs, a person reviewing wants the outputs.
 */
interface Props {
  workOrderId: string | null;
  title?: string | null;
  hideWhenEmpty?: boolean;
  allowUpload?: boolean;
  pollMs?: number | null;
}

export function WorkOrderDocumentsNode({
  workOrderId,
  title,
  hideWhenEmpty = false,
  allowUpload = true,
  pollMs,
}: Props) {
  const [items, setItems] = useState<WorkOrderArtifact[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(() => {
    if (!workOrderId) return;
    void listWorkOrderArtifacts(workOrderId)
      .then(setItems)
      .catch(() => setItems([]));
  }, [workOrderId]);

  useEffect(load, [load]);

  useEffect(() => {
    if (!pollMs) return;
    const tick = () => {
      if (document.visibilityState === "visible") load();
    };
    const timer = setInterval(tick, pollMs);
    return () => clearInterval(timer);
  }, [load, pollMs]);

  const upload = async (files: FileList | null) => {
    if (!workOrderId || !files || files.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      const ids: string[] = [];
      for (const file of Array.from(files)) {
        const result = await uploadDocument({ file, title: file.name });
        for (const document of result.documents) ids.push(document.id);
      }
      // Attached as `input`: a person handing something in, not an agent's output.
      if (ids.length) await attachWorkOrderDocuments(workOrderId, ids, "input");
      load();
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Could not attach that"));
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const detach = async (artifactId: string) => {
    if (!workOrderId) return;
    setBusy(true);
    try {
      await detachWorkOrderArtifact(workOrderId, artifactId);
      load();
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Could not remove that"));
    } finally {
      setBusy(false);
    }
  };

  if (!workOrderId) return <p className="text-sm text-muted-foreground">No work order selected.</p>;
  if (items.length === 0 && hideWhenEmpty) return null;

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        {title ? <h3 className="text-sm font-medium">{title}</h3> : null}
        {allowUpload ? (
          <>
            <input
              ref={fileRef}
              type="file"
              multiple
              className="hidden"
              aria-label="Attach a document"
              onChange={(e) => void upload(e.target.files)}
            />
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="ml-auto"
              disabled={busy}
              onClick={() => fileRef.current?.click()}
            >
              {busy ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <Upload className="mr-1 h-3 w-3" />}
              Attach
            </Button>
          </>
        ) : null}
      </div>
      {error ? <p className="text-xs text-destructive">{error}</p> : null}
      {items.length === 0 ? (
        <p className="text-sm text-muted-foreground">Nothing attached yet.</p>
      ) : (
        <ul className="space-y-1">
          {items.map((item) => (
            <li key={item.id} className="flex items-center gap-2 rounded-md border p-2 text-sm">
              <FileText className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              <span className="min-w-0 flex-1 truncate">{item.title || item.filename || "Untitled"}</span>
              {/* Which direction it went is the useful part. */}
              <span className="shrink-0 rounded border px-1.5 py-0.5 text-[10px] text-muted-foreground">
                {item.kind === "output" ? "produced" : "provided"}
              </span>
              {/* The document can be deleted out from under the link, which is why
                  the filename lives on the artifact row. Say so rather than
                  showing a link to nothing. */}
              {item.missing ? (
                <span className="shrink-0 text-[10px] text-destructive">deleted</span>
              ) : (
                <a
                  href={`/documents/${item.document_id}`}
                  className="shrink-0 text-xs text-muted-foreground hover:text-foreground"
                >
                  Open
                </a>
              )}
              <button
                type="button"
                onClick={() => void detach(item.id)}
                disabled={busy}
                aria-label={`Remove ${item.title || item.filename || "document"}`}
                className="shrink-0 rounded-sm p-0.5 text-muted-foreground hover:text-destructive"
              >
                <Trash2 className="h-3 w-3" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
