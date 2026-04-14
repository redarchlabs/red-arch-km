"use client";

import { FolderIcon, Plus } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { FolderCreate } from "@/components/folders/FolderCreate";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useOrg } from "@/context/OrgContext";
import { listFolders } from "@/lib/api/folders";
import type { Folder } from "@/types";

export default function FoldersPage() {
  const { currentOrgId, isLoading: orgLoading } = useOrg();
  const [folders, setFolders] = useState<Folder[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);

  const load = useCallback(async () => {
    if (!currentOrgId) return;
    setIsLoading(true);
    setError(null);
    try {
      setFolders(await listFolders());
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load folders");
    } finally {
      setIsLoading(false);
    }
  }, [currentOrgId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (orgLoading) return <Skeleton className="h-32 w-full" />;
  if (!currentOrgId) {
    return <p className="text-muted-foreground">Select an organization to view folders.</p>;
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Folders</h1>
          <p className="text-sm text-muted-foreground">
            {folders.length} folder{folders.length === 1 ? "" : "s"} visible
          </p>
        </div>
        <Button onClick={() => setCreateOpen(true)}>
          <Plus className="h-4 w-4" />
          New Folder
        </Button>
      </div>

      {error ? (
        <Card className="border-destructive">
          <CardContent className="pt-6 text-sm text-destructive">{error}</CardContent>
        </Card>
      ) : null}

      {isLoading ? (
        <Skeleton className="h-20 w-full" />
      ) : folders.length > 0 ? (
        <Card>
          <ul className="divide-y">
            {folders.map((folder) => (
              <li key={folder.id} className="flex items-center gap-3 px-6 py-3">
                <FolderIcon className="h-4 w-4 text-muted-foreground" />
                <div>
                  <p className="font-medium">{folder.name}</p>
                  <p className="font-mono text-xs text-muted-foreground">{folder.dot_path}</p>
                </div>
              </li>
            ))}
          </ul>
        </Card>
      ) : (
        <Card>
          <CardContent className="py-12 text-center text-muted-foreground">
            No folders yet. Click <strong>New Folder</strong> to create one.
          </CardContent>
        </Card>
      )}

      <FolderCreate open={createOpen} onClose={() => setCreateOpen(false)} onCreated={load} />
    </div>
  );
}
