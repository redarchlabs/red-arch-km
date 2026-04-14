"use client";

import { ArrowLeft, Trash2 } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  type DocumentChunk,
  deleteDocument,
  getDocument,
  getDocumentChunks,
} from "@/lib/api/documents";
import { formatDate } from "@/lib/format";
import type { Document } from "@/types";

export default function DocumentDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const id = params?.id ?? "";

  const [doc, setDoc] = useState<Document | null>(null);
  const [chunks, setChunks] = useState<DocumentChunk[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

  const load = useCallback(async () => {
    if (!id) return;
    setIsLoading(true);
    setError(null);
    try {
      const [docResult, chunksResult] = await Promise.allSettled([
        getDocument(id),
        getDocumentChunks(id),
      ]);
      if (docResult.status === "fulfilled") setDoc(docResult.value);
      else throw docResult.reason;
      // Chunks may fail if ingestion is still in progress — that's non-fatal
      if (chunksResult.status === "fulfilled") {
        setChunks(chunksResult.value.chunks);
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
    if (!confirm("Delete this document permanently?")) return;
    setDeleting(true);
    try {
      await deleteDocument(id);
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
            <Badge variant={doc.processing_status === "COMPLETE" ? "default" : "secondary"}>
              {doc.processing_status}
            </Badge>
            <span className="text-xs text-muted-foreground">{formatDate(doc.created_at)}</span>
          </div>
        </div>
        <Button variant="destructive" onClick={handleDelete} disabled={deleting}>
          <Trash2 className="h-4 w-4" />
          {deleting ? "Deleting…" : "Delete"}
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>
            Indexed Chunks
            <span className="ml-2 text-sm font-normal text-muted-foreground">
              ({chunks.length})
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
                  <div className="whitespace-pre-wrap text-sm">{chunk.text}</div>
                </li>
              ))}
            </ol>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
