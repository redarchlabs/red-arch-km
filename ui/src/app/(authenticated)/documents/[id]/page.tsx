"use client";

import DOMPurify from "dompurify";
import { ArrowLeft, BookOpen, Pencil, Trash2 } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { Markdown } from "@/components/common/Markdown";
import { DocumentReader } from "@/components/documents/DocumentReader";
import { MarkdownEditor } from "@/components/documents/MarkdownEditor";
import { SummaryTree } from "@/components/documents/SummaryTree";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  type DocumentChunk,
  type DocumentContentResponse,
  type SummaryTreeNode,
  deleteDocument,
  getDocument,
  getDocumentByKey,
  getDocumentChunks,
  getDocumentContent,
  getDocumentSummary,
} from "@/lib/api/documents";
import { formatDate } from "@/lib/format";
import type { Document } from "@/types";

/** Strip any HTML from chunk text — source docs could contain accidental markup. */
function sanitizeChunk(text: string): string {
  return DOMPurify.sanitize(text, { ALLOWED_TAGS: [], ALLOWED_ATTR: [] });
}

export default function DocumentDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const id = params?.id ?? "";

  const [doc, setDoc] = useState<Document | null>(null);
  const [chunks, setChunks] = useState<DocumentChunk[]>([]);
  const [content, setContent] = useState<DocumentContentResponse | null>(null);
  const [summaryTree, setSummaryTree] = useState<SummaryTreeNode | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [readerOpen, setReaderOpen] = useState(false);
  const [editorOpen, setEditorOpen] = useState(false);

  const load = useCallback(async () => {
    if (!id) return;
    setIsLoading(true);
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
      setError(e instanceof Error ? e.message : "Failed to load document");
    } finally {
      setIsLoading(false);
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

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
        </div>
        <div className="flex items-center gap-2">
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
                <li key={chunk.id} className="rounded-md border bg-muted/20 p-3">
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
