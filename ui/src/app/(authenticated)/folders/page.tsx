"use client";

import { ListTree, Plus } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { FolderContents } from "@/components/folders/FolderContents";
import { FolderCreate } from "@/components/folders/FolderCreate";
import { FolderTree } from "@/components/folders/FolderTree";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { MobileDrawer } from "@/components/ui/MobileDrawer";
import { Skeleton } from "@/components/ui/skeleton";
import { useOrg } from "@/context/OrgContext";
import { listFolders, updateFolder } from "@/lib/api/folders";
import type { Folder } from "@/types";

export default function ResourcesPage() {
  const { currentOrgId, isLoading: orgLoading } = useOrg();
  const [folders, setFolders] = useState<Folder[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [treeOpen, setTreeOpen] = useState(false);

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

  const handleMove = useCallback(
    async (folderId: string, newParentId: string | null) => {
      try {
        await updateFolder(folderId, { parent_id: newParentId });
        await load();
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : "Failed to move folder");
      }
    },
    [load],
  );

  const selectedFolder = useMemo(
    () => folders.find((f) => f.id === selectedId) ?? null,
    [folders, selectedId],
  );

  if (orgLoading) return <Skeleton className="h-32 w-full" />;
  if (!currentOrgId) {
    return <p className="text-muted-foreground">Select an organization to view resources.</p>;
  }

  // Selecting a folder in the mobile drawer dismisses it so the contents show.
  const handleSelectFolder = (id: string | null) => {
    setSelectedId(id);
    setTreeOpen(false);
  };

  return (
    <div className="flex min-h-[calc(100vh-7rem)] flex-col gap-3 lg:h-[calc(100vh-7rem)] lg:min-h-0">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          {folders.length > 0 ? (
            <Button
              variant="outline"
              size="icon"
              className="lg:hidden"
              onClick={() => setTreeOpen(true)}
              aria-label="Show folders"
            >
              <ListTree className="h-4 w-4" />
            </Button>
          ) : null}
          <div>
            <h1 className="text-2xl font-semibold">Resources</h1>
            <p className="text-sm text-muted-foreground">
              {folders.length} folder{folders.length === 1 ? "" : "s"} · select a folder to see its
              contents
            </p>
          </div>
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
        <Skeleton className="h-full w-full" />
      ) : folders.length > 0 ? (
        <div className="flex min-h-0 flex-1 gap-3">
          {/* Left: folder tree (navigation by selection). Docked at lg+, a
              drawer opened from the header toggle below lg. */}
          <Card className="hidden w-72 shrink-0 overflow-hidden p-1 lg:block">
            <FolderTree
              folders={folders}
              onMove={handleMove}
              onChanged={load}
              selectedId={selectedId}
              onSelect={setSelectedId}
            />
          </Card>
          <div className="lg:hidden">
            <MobileDrawer
              open={treeOpen}
              onClose={() => setTreeOpen(false)}
              side="left"
              label="Folders"
              className="w-80 max-w-[85%]"
            >
              <div className="min-h-0 flex-1 overflow-y-auto p-2">
                <FolderTree
                  folders={folders}
                  onMove={handleMove}
                  onChanged={load}
                  selectedId={selectedId}
                  onSelect={handleSelectFolder}
                />
              </div>
            </MobileDrawer>
          </div>
          {/* Right: contents of the selected folder */}
          <Card className="min-w-0 flex-1 overflow-hidden">
            <FolderContents
              folder={selectedFolder}
              folders={folders}
              onOpenFolder={setSelectedId}
              onChanged={load}
            />
          </Card>
        </div>
      ) : (
        <Card>
          <CardContent className="py-12 text-center text-muted-foreground">
            No folders yet. Click <strong>New Folder</strong> to create one.
          </CardContent>
        </Card>
      )}

      <FolderCreate
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={load}
        folders={folders}
      />
    </div>
  );
}
