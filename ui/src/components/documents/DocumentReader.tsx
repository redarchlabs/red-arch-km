"use client";

import DOMPurify from "dompurify";
import { Columns2, Loader2, Rows3 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Markdown } from "@/components/common/Markdown";
import { SummaryTree } from "@/components/documents/SummaryTree";
import { Dialog } from "@/components/ui/dialog";
import {
  type DocumentChunk,
  type DocumentContentResponse,
  type SummaryTreeNode,
  getDocumentChunks,
  getDocumentContent,
} from "@/lib/api/documents";

/** How many chunks to pull per lazy-load page. */
const PAGE_SIZE = 50;

/** Strip any HTML — source docs may contain accidental markup. */
function sanitize(text: string): string {
  return DOMPurify.sanitize(text, { ALLOWED_TAGS: [], ALLOWED_ATTR: [] });
}

type ViewMode = "side-by-side" | "embedded";

interface DocumentReaderProps {
  documentId: string;
  documentTitle: string;
  /** Doc-level hierarchical summary; shown in the left rail of side-by-side. */
  summaryTree: SummaryTreeNode | null;
  open: boolean;
  onClose: () => void;
}

/**
 * Full-screen reader for a document. Two views, toggled by the caller:
 *  - "side-by-side": the hierarchical summary tree on the left, the full
 *    document text on the right.
 *  - "embedded": the document text with each chunk's summary inlined above it.
 *
 * Chunks are lazy-loaded a page at a time (a sentinel at the bottom triggers
 * the next page as it scrolls into view), so a very large document — a whole
 * book — never loads every chunk up front.
 */
export function DocumentReader({
  documentId,
  documentTitle,
  summaryTree,
  open,
  onClose,
}: DocumentReaderProps) {
  const [mode, setMode] = useState<ViewMode>("side-by-side");
  const [chunks, setChunks] = useState<DocumentChunk[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The document's ORIGINAL formatted text (null once loaded if the original is
  // binary, e.g. a PDF — then we fall back to the flattened chunk view).
  const [original, setOriginal] = useState<DocumentContentResponse | null>(null);

  // Refs mirror state so the IntersectionObserver callback and the loader
  // always read current values without being re-created on every change.
  const chunksLenRef = useRef(0);
  const totalRef = useRef(0);
  const loadingRef = useRef(false);
  const sentinelRef = useRef<HTMLDivElement | null>(null);

  chunksLenRef.current = chunks.length;
  totalRef.current = total;

  const loadNextPage = useCallback(async () => {
    if (loadingRef.current) return;
    // Nothing more to fetch once we've loaded everything (total known & reached).
    if (totalRef.current > 0 && chunksLenRef.current >= totalRef.current) return;
    loadingRef.current = true;
    setLoading(true);
    setError(null);
    try {
      const offset = chunksLenRef.current;
      const res = await getDocumentChunks(documentId, { offset, limit: PAGE_SIZE });
      setTotal(res.total);
      setChunks((prev) => {
        const seen = new Set(prev.map((c) => c.id));
        return [...prev, ...res.chunks.filter((c) => !seen.has(c.id))];
      });
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load document text");
    } finally {
      loadingRef.current = false;
      setLoading(false);
    }
  }, [documentId]);

  // Reset and load the first page whenever the reader opens (or the doc changes).
  useEffect(() => {
    if (!open) return;
    setChunks([]);
    setTotal(0);
    setError(null);
    setOriginal(null);
    chunksLenRef.current = 0;
    totalRef.current = 0;
    // Fetch the formatted original in parallel; chunks power the embedded view
    // and the fallback when there is no readable original (PDF/images).
    getDocumentContent(documentId)
      .then(setOriginal)
      .catch(() => setOriginal({ content: null, format: null, kind: "other", original_url: null }));
    void loadNextPage();
  }, [open, documentId, loadNextPage]);

  // Lazy-load the next page as the sentinel scrolls into view.
  useEffect(() => {
    if (!open) return;
    const el = sentinelRef.current;
    if (!el) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) void loadNextPage();
      },
      { rootMargin: "300px" },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [open, mode, loadNextPage]);

  const hasMore = total === 0 || chunks.length < total;

  return (
    <Dialog open={open} onClose={onClose} className="flex h-[90vh] max-w-6xl flex-col p-0">
      <header className="flex items-center gap-3 border-b px-5 py-3 pr-14">
        <h2 className="min-w-0 flex-1 truncate text-lg font-semibold" title={documentTitle}>
          {documentTitle}
        </h2>
        <div className="flex rounded-md border p-0.5">
          <ModeButton
            active={mode === "side-by-side"}
            onClick={() => setMode("side-by-side")}
            icon={<Columns2 className="h-4 w-4" />}
            label="Side-by-side"
          />
          <ModeButton
            active={mode === "embedded"}
            onClick={() => setMode("embedded")}
            icon={<Rows3 className="h-4 w-4" />}
            label="Embedded"
          />
        </div>
      </header>

      {error ? (
        <div className="border-b bg-destructive/10 px-5 py-2 text-sm text-destructive">{error}</div>
      ) : null}

      {mode === "side-by-side" ? (
        <div className="flex min-h-0 flex-1">
          <aside className="hidden w-2/5 min-w-0 overflow-y-auto border-r p-4 md:block">
            <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Summary
            </div>
            {summaryTree ? (
              <SummaryTree root={summaryTree} />
            ) : (
              <p className="text-sm text-muted-foreground">No summary available.</p>
            )}
          </aside>
          {original?.kind === "pdf" && original.original_url ? (
            // Native PDF rendering — perfect formatting, no chunk fallback.
            <iframe
              title="Original document"
              src={original.original_url}
              className="min-h-0 flex-1"
            />
          ) : (
            <div className="min-h-0 flex-1 overflow-y-auto p-5">
              <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Full text
              </div>
              {original?.content ? (
                // The document's original formatting, rendered readably. No chunk
                // pagination here — the source is served whole.
                original.format === "markdown" ? (
                  <Markdown content={original.content} />
                ) : (
                  <div className="whitespace-pre-wrap text-sm leading-relaxed">
                    {original.content}
                  </div>
                )
              ) : original?.kind === "image" && original.original_url ? (
                // Dynamic short-lived signed URL — next/image can't optimize it.
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={original.original_url}
                  alt="Original document"
                  className="mx-auto max-w-full"
                />
              ) : (
                <>
                  <FullText chunks={chunks} />
                  <div ref={sentinelRef}>
                    <LoadSentinel
                      loading={loading}
                      hasMore={hasMore}
                      loaded={chunks.length}
                      total={total}
                    />
                  </div>
                </>
              )}
            </div>
          )}
        </div>
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto p-5">
          <ol className="space-y-5">
            {chunks.map((chunk) => (
              <li key={chunk.id}>
                {chunk.summary ? (
                  <div className="mb-1.5 rounded-md border-l-2 border-primary/60 bg-muted/40 px-3 py-1.5 text-sm italic text-muted-foreground">
                    {sanitize(chunk.summary)}
                  </div>
                ) : null}
                <div className="whitespace-pre-wrap text-sm leading-relaxed">
                  {sanitize(chunk.text)}
                </div>
              </li>
            ))}
          </ol>
          <div ref={sentinelRef}>
            <LoadSentinel loading={loading} hasMore={hasMore} loaded={chunks.length} total={total} />
          </div>
        </div>
      )}
    </Dialog>
  );
}

interface ModeButtonProps {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
}

function ModeButton({ active, onClick, icon, label }: ModeButtonProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`flex items-center gap-1.5 rounded px-2.5 py-1 text-sm ${
        active ? "bg-accent text-foreground" : "text-muted-foreground hover:text-foreground"
      }`}
    >
      {icon}
      <span className="hidden sm:inline">{label}</span>
    </button>
  );
}

/** Document text rendered as continuous reading order (one block per chunk). */
function FullText({ chunks }: { chunks: DocumentChunk[] }) {
  return (
    <div className="space-y-3">
      {chunks.map((chunk) => (
        <p key={chunk.id} className="whitespace-pre-wrap text-sm leading-relaxed">
          {sanitize(chunk.text)}
        </p>
      ))}
    </div>
  );
}

interface LoadSentinelProps {
  loading: boolean;
  hasMore: boolean;
  loaded: number;
  total: number;
}

/** Bottom-of-list marker; the parent puts the observed ref on its wrapper. */
function LoadSentinel({ loading, hasMore, loaded, total }: LoadSentinelProps) {
  return (
    <div className="py-4 text-center text-xs text-muted-foreground">
      {loading ? (
        <span className="inline-flex items-center gap-1.5">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          Loading…
        </span>
      ) : hasMore ? (
        <span>Scroll to load more</span>
      ) : total > 0 ? (
        <span>
          {loaded} of {total} sections
        </span>
      ) : (
        <span>No content yet.</span>
      )}
    </div>
  );
}
