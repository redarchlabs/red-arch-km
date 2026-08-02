"use client";

import { Columns2, ExternalLink, Loader2, Rows3 } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

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
import { segmentOriginalByChunks } from "@/lib/readerSegments";
import { cn } from "@/lib/utils";

/** How many chunks to pull per lazy-load page. */
const PAGE_SIZE = 50;

type ViewMode = "side-by-side" | "embedded";

interface DocumentReaderProps {
  documentId: string;
  documentTitle: string;
  /** Doc-level hierarchical summary; shown in the left rail of side-by-side. */
  summaryTree: SummaryTreeNode | null;
  open: boolean;
  onClose: () => void;
  /**
   * Chunk to scroll to and highlight — set when the reader is reached via a
   * chat-citation deep-link (`#chunk-<order>`). The reader pages through the
   * lazy-loaded chunks until the target is present.
   */
  targetChunkOrder?: number | null;
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
  targetChunkOrder = null,
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
  // The summaries rail paginates independently of the full text: when a readable
  // original is shown the right pane has no chunk sentinel, so the summaries
  // need their own trigger or they'd stay stuck at the first page.
  const summarySentinelRef = useRef<HTMLDivElement | null>(null);
  // Side-by-side panes are scroll-synced so a summary stays aligned with the
  // text it summarizes.
  const leftScrollRef = useRef<HTMLDivElement | null>(null);
  const rightScrollRef = useRef<HTMLDivElement | null>(null);

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

  // Lazy-load the next page as either sentinel scrolls into view. Both the
  // full-text sentinel (fallback/embedded views) and the summaries-rail sentinel
  // feed the same paginated chunk list, so we observe whichever are mounted.
  const hasChunks = chunks.length > 0;
  useEffect(() => {
    if (!open) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) void loadNextPage();
      },
      { rootMargin: "300px" },
    );
    const targets = [sentinelRef.current, summarySentinelRef.current].filter(
      (el): el is HTMLDivElement => el !== null,
    );
    for (const el of targets) observer.observe(el);
    return () => observer.disconnect();
  }, [open, mode, loadNextPage, hasChunks, original]);

  // Proportionally sync the two side-by-side panes so scrolling the text moves
  // the summaries to the matching position (and vice versa).
  useEffect(() => {
    if (!open || mode !== "side-by-side") return;
    const left = leftScrollRef.current;
    const right = rightScrollRef.current;
    if (!left || !right) return; // right is absent for the PDF iframe — no sync
    let lock = false;
    const sync = (from: HTMLDivElement, to: HTMLDivElement) => {
      if (lock) return;
      lock = true;
      const fromMax = from.scrollHeight - from.clientHeight;
      const toMax = to.scrollHeight - to.clientHeight;
      to.scrollTop = fromMax > 0 ? (from.scrollTop / fromMax) * toMax : 0;
      requestAnimationFrame(() => {
        lock = false;
      });
    };
    const onLeft = () => sync(left, right);
    const onRight = () => sync(right, left);
    left.addEventListener("scroll", onLeft, { passive: true });
    right.addEventListener("scroll", onRight, { passive: true });
    return () => {
      left.removeEventListener("scroll", onLeft);
      right.removeEventListener("scroll", onRight);
    };
  }, [open, mode, chunks.length, original]);

  // When opened via a citation deep-link, keep pulling pages until the cited
  // chunk is loaded (chunks arrive lazily, PAGE_SIZE at a time). Terminates
  // once the target is present or every chunk is loaded without a match.
  const targetLoaded =
    targetChunkOrder != null && chunks.some((c) => c.chunk_order === targetChunkOrder);
  useEffect(() => {
    if (!open || targetChunkOrder == null || targetLoaded) return;
    if (total > 0 && chunks.length >= total) return;
    void loadNextPage();
  }, [open, targetChunkOrder, targetLoaded, chunks.length, total, loadNextPage]);

  // Scroll the cited chunk into view once per open — the full-text block when
  // rendered, otherwise its summary card (side-by-side over a readable
  // original; pane-sync then aligns the text). The highlight itself is styling
  // on the matching elements and persists while the reader stays open.
  const scrolledToTarget = useRef(false);
  useEffect(() => {
    scrolledToTarget.current = false;
  }, [open, targetChunkOrder]);
  useEffect(() => {
    if (!open || targetChunkOrder == null || !targetLoaded || scrolledToTarget.current) return;
    const el =
      document.getElementById(`reader-chunk-${targetChunkOrder}`) ??
      document.getElementById(`reader-summary-${targetChunkOrder}`);
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    scrolledToTarget.current = true;
  }, [open, targetChunkOrder, targetLoaded, mode, original]);

  const hasMore = total === 0 || chunks.length < total;

  return (
    <Dialog open={open} onClose={onClose} className="flex h-[90vh] max-w-6xl flex-col p-0">
      <header className="flex items-center gap-3 border-b px-5 py-3 pr-14">
        <h2 className="min-w-0 flex-1 truncate text-lg font-semibold" title={documentTitle}>
          {documentTitle}
        </h2>
        {original?.original_url ? (
          // The extracted text is scroll-synced with the summaries; this opens
          // the untouched original (PDF/image) in a new tab for pixel fidelity.
          <a
            href={original.original_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-sm text-muted-foreground hover:text-foreground"
          >
            <ExternalLink className="h-4 w-4" />
            Original
          </a>
        ) : null}
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
          <aside
            ref={leftScrollRef}
            className="hidden w-2/5 min-w-0 overflow-y-auto border-r p-4 md:block"
          >
            <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Section summaries
            </div>
            {chunks.length > 0 ? (
              // Per-section summaries in document order; scroll-synced with the
              // text on the right so each stays aligned with what it summarizes.
              // Paginates via its own sentinel, independent of the full text.
              <>
                <ol className="space-y-2">
                  {chunks.map((chunk) => (
                    <li
                      key={chunk.id}
                      id={`reader-summary-${chunk.chunk_order}`}
                      className={cn(
                        "rounded-md border p-2",
                        chunk.chunk_order === targetChunkOrder
                          ? "border-primary bg-primary/10"
                          : "bg-muted/20",
                      )}
                    >
                      <div className="mb-0.5 text-xs font-medium text-muted-foreground">
                        Section {chunk.chunk_order + 1}
                      </div>
                      <div className="text-sm text-muted-foreground">{chunk.summary || "—"}</div>
                    </li>
                  ))}
                </ol>
                <div ref={summarySentinelRef}>
                  <LoadSentinel
                    loading={loading}
                    hasMore={hasMore}
                    loaded={chunks.length}
                    total={total}
                  />
                </div>
              </>
            ) : summaryTree ? (
              <SummaryTree root={summaryTree} />
            ) : (
              <p className="text-sm text-muted-foreground">No summary available.</p>
            )}
          </aside>
          <div ref={rightScrollRef} className="min-h-0 flex-1 overflow-y-auto p-5">
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
                  <FullText chunks={chunks} targetChunkOrder={targetChunkOrder} />
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
        </div>
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto p-5">
          {original?.content ? (
            // Summaries inlined into the ORIGINAL source, so Markdown renders as
            // headings/lists/tables instead of flattened chunk text.
            <EmbeddedOriginal
              content={original.content}
              format={original.format}
              chunks={chunks}
              targetChunkOrder={targetChunkOrder}
            />
          ) : (
            // No readable original (PDF/image/OCR): the chunk text is all there
            // is, and it is whitespace-flattened, so it stays unformatted.
            <ol className="space-y-5">
              {chunks.map((chunk) => (
                <li
                  key={chunk.id}
                  id={`reader-chunk-${chunk.chunk_order}`}
                  className={cn(
                    chunk.chunk_order === targetChunkOrder &&
                      "rounded-md border border-primary bg-primary/10 p-3",
                  )}
                >
                  {chunk.summary ? (
                    <div className="mb-1.5 rounded-md border-l-2 border-primary/60 bg-muted/40 px-3 py-1.5 text-sm italic text-muted-foreground">
                      {chunk.summary}
                    </div>
                  ) : null}
                  <div className="whitespace-pre-wrap text-sm leading-relaxed">{chunk.text}</div>
                </li>
              ))}
            </ol>
          )}
          {/* Shared by both branches — more summaries page in as it is reached. */}
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

interface EmbeddedOriginalProps {
  content: string;
  format: DocumentContentResponse["format"];
  chunks: DocumentChunk[];
  targetChunkOrder?: number | null;
}

/**
 * Embedded view over a readable original: the source is cut at the loaded
 * chunks' boundaries and each slice is introduced by that chunk's summary.
 * Markdown slices render formatted; plain text keeps its line breaks.
 */
function EmbeddedOriginal({ content, format, chunks, targetChunkOrder }: EmbeddedOriginalProps) {
  const segments = useMemo(() => segmentOriginalByChunks(content, chunks), [content, chunks]);
  return (
    <ol className="space-y-5">
      {segments.map((segment, index) => {
        const isTarget = segment.summaries.some((s) => s.chunkOrder === targetChunkOrder);
        return (
          <li
            key={`${index}-${segment.summaries[0]?.chunkOrder ?? "lead"}`}
            className={cn(isTarget && "rounded-md border border-primary bg-primary/10 p-3")}
          >
            {segment.summaries.map((s) => (
              // The citation deep-link scrolls to this id (see the target effect).
              <div
                key={s.chunkOrder}
                id={`reader-chunk-${s.chunkOrder}`}
                className="mb-1.5 rounded-md border-l-2 border-primary/60 bg-muted/40 px-3 py-1.5 text-sm italic text-muted-foreground"
              >
                {s.summary || `Section ${s.chunkOrder + 1}`}
              </div>
            ))}
            {format === "markdown" ? (
              <Markdown content={segment.text} />
            ) : (
              <div className="whitespace-pre-wrap text-sm leading-relaxed">{segment.text}</div>
            )}
          </li>
        );
      })}
    </ol>
  );
}

/** Document text rendered as continuous reading order (one block per chunk). */
function FullText({
  chunks,
  targetChunkOrder,
}: {
  chunks: DocumentChunk[];
  targetChunkOrder?: number | null;
}) {
  return (
    <div className="space-y-3">
      {chunks.map((chunk) => (
        <p
          key={chunk.id}
          id={`reader-chunk-${chunk.chunk_order}`}
          className={cn(
            "whitespace-pre-wrap text-sm leading-relaxed",
            chunk.chunk_order === targetChunkOrder &&
              "rounded-md border border-primary bg-primary/10 p-3",
          )}
        >
          {chunk.text}
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
