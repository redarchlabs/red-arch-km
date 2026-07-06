import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { listDocuments } from "@/lib/api/documents";
import type { Document } from "@/types";

import { isProcessing } from "./explorerItem";

interface FolderDocuments {
  docs: Document[];
  loading: boolean;
  error: string | null;
  /** Re-fetch the folder's documents. `silent` skips the spinner (polling). */
  reload: (silent?: boolean) => Promise<void>;
}

/**
 * Load (and poll) the documents for a folder.
 *
 * Guards against a stale-closure race: switching folders quickly can leave an
 * older fetch in flight, and its late resolution must not overwrite the current
 * folder's contents. Every call bumps a monotonic request id; a resolved
 * response only commits to state if it is still the latest request (mirrors the
 * `cancelled` flag in MarkdownEditor's load effect).
 *
 * `silent` refreshes (used by polling) skip the loading spinner and keep the
 * current rows on error so the list doesn't flicker or blank out.
 */
export function useFolderDocuments(folderId: string | null): FolderDocuments {
  const [docs, setDocs] = useState<Document[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestIdRef = useRef(0);

  const reload = useCallback(
    async (silent = false) => {
      // Claim this request's id up front so a folder switch mid-flight (which
      // recreates this callback and triggers a newer load) invalidates us.
      const requestId = ++requestIdRef.current;
      if (!folderId) {
        setDocs([]);
        return;
      }
      if (!silent) setLoading(true);
      try {
        // The API caps page_size at 200 (PaginationParams), so pull every page
        // and accumulate — the list is virtualized, so holding all rows in
        // memory is cheap and the user can scroll the whole folder.
        const pageSize = 200;
        const first = await listDocuments(1, pageSize, folderId);
        const all = [...first.items];
        const totalPages = Math.max(1, Math.ceil(first.total / pageSize));
        for (let page = 2; page <= totalPages; page += 1) {
          const next = await listDocuments(page, pageSize, folderId);
          if (next.items.length === 0) break; // safety against a shifting total
          all.push(...next.items);
        }
        if (requestId !== requestIdRef.current) return; // stale — a newer load won
        setDocs(all);
        setError(null);
      } catch (e) {
        if (requestId !== requestIdRef.current) return; // stale — ignore its failure
        // Surface the failure instead of masking it as an empty folder.
        if (!silent) {
          setDocs([]);
          setError(e instanceof Error ? e.message : "Failed to load documents");
        }
      } finally {
        if (!silent && requestId === requestIdRef.current) setLoading(false);
      }
    },
    [folderId],
  );

  useEffect(() => {
    void reload();
  }, [reload]);

  // While any document is still processing, poll quietly so its status flips to
  // ready (or failed) without a manual refresh. Stops once nothing is pending.
  const anyProcessing = useMemo(() => docs.some((d) => isProcessing(d.processing_status)), [docs]);
  useEffect(() => {
    if (!anyProcessing) return;
    const timer = setInterval(() => void reload(true), 4000);
    return () => clearInterval(timer);
  }, [anyProcessing, reload]);

  return { docs, loading, error, reload };
}
