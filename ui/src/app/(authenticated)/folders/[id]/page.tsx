"use client";

import { ArrowLeft, Plus } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { DocumentUpload } from "@/components/documents/DocumentUpload";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useOrg } from "@/context/OrgContext";
import { listDocuments } from "@/lib/api/documents";
import { getFolder } from "@/lib/api/folders";
import { formatDate } from "@/lib/format";
import type { Document, Folder, PaginatedResponse } from "@/types";

function statusVariant(status: Document["processing_status"]) {
  if (status === "SUCCESS") return "default";
  if (status === "FAILED") return "destructive";
  return "secondary";
}

export default function FolderDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id ?? "";
  const { currentOrgId, isLoading: orgLoading } = useOrg();

  const [folder, setFolder] = useState<Folder | null>(null);
  const [data, setData] = useState<PaginatedResponse<Document> | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);

  const load = useCallback(async () => {
    if (!id || !currentOrgId) return;
    setIsLoading(true);
    setError(null);
    try {
      const [folderResult, docsResult] = await Promise.all([
        getFolder(id),
        listDocuments(1, 20, id),
      ]);
      setFolder(folderResult);
      setData(docsResult);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load folder");
    } finally {
      setIsLoading(false);
    }
  }, [id, currentOrgId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (orgLoading) return <Skeleton className="h-32 w-full" />;

  return (
    <div className="space-y-4">
      <Link
        href="/folders"
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="h-3 w-3" />
        Back to folders
      </Link>

      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">{folder?.name ?? "Folder"}</h1>
          <p className="text-sm text-muted-foreground">
            {folder?.dot_path}
            {data ? ` · ${data.total} document${data.total === 1 ? "" : "s"}` : ""}
          </p>
        </div>
        <Button onClick={() => setUploadOpen(true)}>
          <Plus className="h-4 w-4" />
          New Document
        </Button>
      </div>

      {error ? (
        <Card className="border-destructive">
          <CardContent className="pt-6 text-sm text-destructive">{error}</CardContent>
        </Card>
      ) : null}

      {isLoading ? (
        <div className="space-y-2">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-20 w-full" />
          ))}
        </div>
      ) : data && data.items.length > 0 ? (
        <div className="space-y-2">
          {data.items.map((doc) => (
            <Link key={doc.id} href={`/documents/${doc.id}`} className="block">
              <Card className="transition-colors hover:bg-accent/30">
                <CardContent className="flex items-center justify-between py-4">
                  <div>
                    <h3 className="font-medium">{doc.title}</h3>
                    {doc.description ? (
                      <p className="mt-0.5 text-sm text-muted-foreground">{doc.description}</p>
                    ) : null}
                    <p className="mt-1 text-xs text-muted-foreground">{formatDate(doc.created_at)}</p>
                  </div>
                  <Badge variant={statusVariant(doc.processing_status)}>{doc.processing_status}</Badge>
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      ) : (
        <Card>
          <CardContent className="py-12 text-center text-muted-foreground">
            This folder is empty. Click <strong>New Document</strong> to add one.
          </CardContent>
        </Card>
      )}

      <DocumentUpload
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        onCreated={load}
        defaultFolderId={id}
      />
    </div>
  );
}
