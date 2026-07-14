"use client";

import DOMPurify from "dompurify";
import { ArrowLeft, Ban, BookOpen, Pencil, RefreshCw, Trash2 } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { Markdown } from "@/components/common/Markdown";
import { DocumentReader } from "@/components/documents/DocumentReader";
import { IngestProgress } from "@/components/documents/IngestProgress";
import { JobLogs } from "@/components/documents/JobLogs";
import { MarkdownEditor } from "@/components/documents/MarkdownEditor";
import { SummaryTree } from "@/components/documents/SummaryTree";
import { isProcessing } from "@/components/folders/explorerItem";
import { useOrg } from "@/context/OrgContext";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  type DocumentChunk,
  type DocumentContentResponse,
  type SummaryTreeNode,
  cancelDocumentIngest,
  deleteDocument,
  getDocument,
  getDocumentByKey,
  getDocumentChunks,
  getDocumentContent,
  getDocumentSummary,
  reprocessDocument,
} from "@/lib/api/documents";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Document } from "@/types";

/** Strip any HTML from chunk text — source docs could contain accidental markup. */
function sanitizeChunk(text: string): string {
  return DOMPurify.sanitize(text, { ALLOWED_TAGS: [], ALLOWED_ATTR: [] });
}

export default function DocumentDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const { isOrgAdmin } = useOrg();
  const id = params?.id ?? "";

  const [doc, setDoc] = useState<Document | null>(null);
  const [chunks, setChunks] = useState<DocumentChunk[]>([]);
  const [content, setContent] = useState<DocumentContentResponse | null>(null);
  const [summaryTree, setSummaryTree] = useState<SummaryTreeNode | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [reprocessing, setReprocessing] = useState(false);
  const [readerOpen, setReaderOpen] = useState(false);
  const [editorOpen, setEditorOpen] = useState(false);
  // Chunk to briefly highlight when arrived-at via a chat citation deep-link
  // (`/documents/<key>#chunk-<order>`).
  const [highlightOrder, setHighlightOrder] = useState<number | null>(null);

  // `silent` refreshes (polling while an ingest runs) skip the spinner so the
  // page doesn't blank out every few seconds.
  const load = useCallback(async (silent = false) => {
    if (!id) return;
    if (!silent) setIsLoading(true);
    setError(null);
    try {
      // The URL param is usually a Postgres id, but chat/search links reference
      // documents by their vector-store key — resolve that on a 404.
      let document: Document;
      try {
        document = await getDocument(id);
      } catch {
        document = await getDocumentByKey(id);
      }
      setDoc(document);
      // Load sub-resources by the RESOLVED id (the URL param may be a key).
      const docId = document.id;
      const [chunksResult, summaryResult, contentResult] = await Promise.allSettled([
        getDocumentChunks(docId),
        getDocumentSummary(docId),
        getDocumentContent(docId),
      ]);
      // Chunks may fail if ingestion is still in progress — that's non-fatal
      if (chunksResult.status === "fulfilled") {
        setChunks(chunksResult.value.chunks);
      }
      // Original formatted text (null for PDFs/images) — non-fatal if missing.
      if (contentResult.status === "fulfilled") {
        setContent(contentResult.value);
      }
      // Summary tree isn't written until the doc-level record is upserted at
      // the end of ingestion — a 404/failure here just means "not ready yet".
      if (summaryResult.status === "fulfilled") {
        setSummaryTree(summaryResult.value.summary_tree);
      }
    } catch (e: unknown) {
      // A silent poll failure keeps the last-known document on screen.
      if (!silent) setError(e instanceof Error ? e.message : "Failed to load document");
    } finally {
      if (!silent) setIsLoading(false);
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  // Land in the READ view by default: once a finished document has loaded, open
  // the full-screen reader (the detail/manage page — chunks, reprocess, summary —
  // sits behind it). Fires once per mount, so closing the reader doesn't reopen it,
  // and a still-processing document isn't yanked into an empty reader.
  const autoOpenedReader = useRef(false);
  useEffect(() => {
    if (autoOpenedReader.current) return;
    if (doc && doc.processing_status === "SUCCESS") {
      autoOpenedReader.current = true;
      setReaderOpen(true);
    }
  }, [doc]);

  // While the ingest is still running, poll quietly so the progress bar, status,
  // logs, and (on completion) chunks/summary refresh without a manual reload.
  const processing = doc ? isProcessing(doc.processing_status) : false;
  useEffect(() => {
    if (!processing) return;
    const timer = setInterval(() => void load(true), 4000);
    return () => clearInterval(timer);
  }, [processing, load]);

  // When linked from a chat citation (`#chunk-<order>`), scroll the matching
  // indexed chunk into view and flash a highlight. Runs once chunks are loaded.
  useEffect(() => {
    if (chunks.length === 0) return;
    const match = window.location.hash.match(/^#chunk-(\d+)$/);
    if (!match) return;
    const order = Number(match[1]);
    const el = document.getElementById(`chunk-${order}`);
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    setHighlightOrder(order);
    const timer = setTimeout(() => setHighlightOrder(null), 2500);
    return () => clearTimeout(timer);
  }, [chunks]);

  const handleCancel = async () => {
    if (!doc || !window.confirm("Cancel this ingest? Any partial index for it is discarded.")) return;
    setCancelling(true);
    try {
      await cancelDocumentIngest(doc.id);
      await load(true);
    } catch (e: unknown) {
      window.alert(e instanceof Error ? e.message : "Cancel failed");
    } finally {
      setCancelling(false);
    }
  };

  const handleReprocess = async () => {
    if (!doc || !window.confirm("Reprocess this document? Its current index is rebuilt from the original.")) return;
    setReprocessing(true);
    try {
      await reprocessDocument(doc.id);
      await load(true);
    } catch (e: unknown) {
      window.alert(e instanceof Error ? e.message : "Reprocess failed");
    } finally {
      setReprocessing(false);
    }
  };

  const handleDelete = async () => {
    if (!doc || !confirm("Delete this document permanently?")) return;
    setDeleting(true);
    try {
      await deleteDocument(doc.id);
      router.push("/documents");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Delete failed");
      setDeleting(false);
    }
  };

  if (isLoading) {
    return <Skeleton className="h-64 w-full" />;
  }

  if (error || !doc) {
    return (
      <div className="space-y-4">
        <Link href="/documents" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="h-3 w-3" />
          Back to documents
        </Link>
        <Card className="border-destructive">
          <CardContent className="pt-6 text-sm text-destructive">
            {error ?? "Document not found"}
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <Link href="/documents" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="h-3 w-3" />
        Back to documents
      </Link>

      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-semibold">{doc.title}</h1>
          {doc.description ? (
            <p className="mt-1 text-muted-foreground">{doc.description}</p>
          ) : null}
          <div className="mt-2 flex items-center gap-2">
            <Badge
              variant={
                doc.processing_status === "SUCCESS"
                  ? "default"
                  : doc.processing_status === "FAILED"
                    ? "destructive"
                    : "secondary"
              }
            >
              {doc.processing_status}
            </Badge>
            <span className="text-xs text-muted-foreground">{formatDate(doc.created_at)}</span>
          </div>
          {processing ? (
            <IngestProgress
              status={doc.processing_status}
              details={doc.processing_details}
              className="mt-3 max-w-sm"
            />
          ) : null}
        </div>
        <div className="flex items-center gap-2">
          {processing ? (
            <Button variant="outline" onClick={handleCancel} disabled={cancelling}>
              <Ban className="h-4 w-4" />
              {cancelling ? "Cancelling…" : "Cancel ingest"}
            </Button>
          ) : isOrgAdmin ? (
            <Button variant="outline" onClick={handleReprocess} disabled={reprocessing}>
              <RefreshCw className="h-4 w-4" />
              {reprocessing ? "Reprocessing…" : "Reprocess"}
            </Button>
          ) : null}
          <Button variant="outline" onClick={() => setReaderOpen(true)}>
            <BookOpen className="h-4 w-4" />
            Read document
          </Button>
          {content?.kind === "markdown" || content?.kind === "text" ? (
            <Button variant="outline" onClick={() => setEditorOpen(true)}>
              <Pencil className="h-4 w-4" />
              Edit
            </Button>
          ) : null}
          <Button variant="destructive" onClick={handleDelete} disabled={deleting}>
            <Trash2 className="h-4 w-4" />
            {deleting ? "Deleting…" : "Delete"}
          </Button>
        </div>
      </div>

      <DocumentReader
        documentId={doc.id}
        documentTitle={doc.title}
        summaryTree={summaryTree}
        open={readerOpen}
        onClose={() => setReaderOpen(false)}
      />

      {editorOpen ? (
        <MarkdownEditor
          open
          documentId={doc.id}
          initialTitle={doc.title}
          onClose={() => setEditorOpen(false)}
          onSaved={() => void load()}
        />
      ) : null}

      {content?.content ? (
        <Card>
          <CardHeader>
            <CardTitle>Document</CardTitle>
          </CardHeader>
          <CardContent>
            {content.format === "markdown" ? (
              <Markdown content={content.content} />
            ) : (
              <div className="whitespace-pre-wrap text-sm leading-relaxed">{content.content}</div>
            )}
          </CardContent>
        </Card>
      ) : null}

      {isOrgAdmin ? <JobLogs documentId={doc.id} poll={processing} /> : null}

      <Card>
        <CardHeader>
          <CardTitle>Document Summary Tree</CardTitle>
        </CardHeader>
        <CardContent>
          {summaryTree ? (
            <SummaryTree root={summaryTree} />
          ) : (
            <p className="text-sm text-muted-foreground">
              No summary yet. The document may still be processing.
            </p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base font-medium text-muted-foreground">
            Indexed chunks
            <span className="ml-2 text-sm font-normal">
              ({chunks.length}) — how retrieval sees this document
            </span>
          </CardTitle>
        </CardHeader>
        <CardContent>
          {chunks.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No chunks yet. The document may still be processing.
            </p>
          ) : (
            <ol className="space-y-3">
              {chunks.map((chunk) => (
                <li
                  key={chunk.id}
                  id={`chunk-${chunk.chunk_order}`}
                  className={cn(
                    "scroll-mt-24 rounded-md border p-3 transition-colors duration-500",
                    highlightOrder === chunk.chunk_order
                      ? "border-primary bg-primary/10"
                      : "bg-muted/20",
                  )}
                >
                  <div className="mb-1 text-xs font-medium text-muted-foreground">
                    Chunk #{chunk.chunk_order}
                  </div>
                  <div className="whitespace-pre-wrap text-sm">
                    {sanitizeChunk(chunk.text)}
                  </div>
                </li>
              ))}
            </ol>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
