"use client";

import { Plus } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { DocumentUpload } from "@/components/documents/DocumentUpload";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useOrg } from "@/context/OrgContext";
import { listDocuments } from "@/lib/api/documents";
import { formatDate } from "@/lib/format";
import type { Document, PaginatedResponse } from "@/types";

function statusVariant(status: Document["processing_status"]) {
  if (status === "SUCCESS") return "default";
  if (status === "FAILED") return "destructive";
  return "secondary";
}

export default function DocumentsPage() {
  const { currentOrgId, isLoading: orgLoading } = useOrg();
  const [data, setData] = useState<PaginatedResponse<Document> | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);

  const load = useCallback(async () => {
    if (!currentOrgId) return;
    setIsLoading(true);
    setError(null);
    try {
      setData(await listDocuments());
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
  // without a manual refresh. Stops once everything has settled.
  useEffect(() => {
    const inFlight = data?.items.some(
      (d) => d.processing_status === "PENDING" || d.processing_status === "PROCESSING",
    );
    if (!inFlight) return;
    const timer = setInterval(() => void load(), 5000);
    return () => clearInterval(timer);
  }, [data, load]);

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
            <Link key={doc.id} href={`/documents/${doc.id}`} className="block">
              <Card className="transition-colors hover:bg-accent/30">
                <CardContent className="flex items-center justify-between py-4">
                  <div>
                    <h3 className="font-medium">{doc.title}</h3>
                    {doc.description ? (
                      <p className="mt-0.5 text-sm text-muted-foreground">{doc.description}</p>
                    ) : null}
                    <p className="mt-1 text-xs text-muted-foreground">
                      {formatDate(doc.created_at)}
                    </p>
                  </div>
                  <Badge variant={statusVariant(doc.processing_status)}>
                    {doc.processing_status}
                  </Badge>
                </CardContent>
              </Card>
            </Link>
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
