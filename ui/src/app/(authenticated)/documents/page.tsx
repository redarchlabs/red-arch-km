"use client";

import { Plus } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { DocumentRow } from "@/components/documents/DocumentRow";
import { DocumentUpload } from "@/components/documents/DocumentUpload";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useOrg } from "@/context/OrgContext";
import { listDocuments } from "@/lib/api/documents";
import { listFolders } from "@/lib/api/folders";
import type { Document, Folder, PaginatedResponse } from "@/types";

export default function DocumentsPage() {
  const { currentOrgId, isLoading: orgLoading } = useOrg();
  const [data, setData] = useState<PaginatedResponse<Document> | null>(null);
  const [folders, setFolders] = useState<Folder[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);

  const load = useCallback(async () => {
    if (!currentOrgId) return;
    setIsLoading(true);
    setError(null);
    try {
      // Folders power the Properties dialog's folder picker; a folder-list
      // failure shouldn't block the document list.
      const [docs, folderList] = await Promise.allSettled([listDocuments(), listFolders()]);
      if (docs.status === "fulfilled") setData(docs.value);
      else throw docs.reason;
      if (folderList.status === "fulfilled") setFolders(folderList.value);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load documents");
    } finally {
      setIsLoading(false);
    }
  }, [currentOrgId]);

  useEffect(() => {
    void load();
  }, [load]);

  // Ingestion is async (worker → status callback). Poll while any document is
  // still PENDING/PROCESSING so the status badge flips to SUCCESS/FAILED
  // without a manual refresh. Depend on a stable boolean (not `data`) so the
  // interval isn't torn down and rebuilt on every tick — it stays put until
  // nothing is in flight.
  const hasInFlight =
    data?.items.some(
      (d) => d.processing_status === "PENDING" || d.processing_status === "PROCESSING",
    ) ?? false;
  useEffect(() => {
    if (!hasInFlight) return;
    const timer = setInterval(() => void load(), 5000);
    return () => clearInterval(timer);
  }, [hasInFlight, load]);

  if (orgLoading) {
    return <Skeleton className="h-32 w-full" />;
  }

  if (!currentOrgId) {
    return <p className="text-muted-foreground">Select an organization to view documents.</p>;
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Documents</h1>
          <p className="text-sm text-muted-foreground">
            {data ? `${data.total} document${data.total === 1 ? "" : "s"}` : "Loading…"}
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
            <DocumentRow key={doc.id} doc={doc} folders={folders} onChanged={load} />
          ))}
        </div>
      ) : (
        <Card>
          <CardContent className="py-12 text-center text-muted-foreground">
            No documents yet. Click <strong>New Document</strong> to add one.
          </CardContent>
        </Card>
      )}

      <DocumentUpload
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        onCreated={load}
      />
    </div>
  );
}
