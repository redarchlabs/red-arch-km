"use client";

import {
  FilePlus2,
  FolderPlus,
  LayoutGrid,
  List as ListIcon,
  Rows3,
  Settings2,
  TextCursorInput,
  Upload,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useMemo, useState } from "react";

import { DocumentProperties } from "@/components/documents/DocumentProperties";
import { DocumentUpload } from "@/components/documents/DocumentUpload";
import { MarkdownEditor } from "@/components/documents/MarkdownEditor";
import { useRowMenu } from "@/components/common/ActionMenu";
import { FolderCreate } from "@/components/folders/FolderCreate";
import { FolderProperties } from "@/components/folders/FolderProperties";
import { cn } from "@/lib/utils";
import type { Folder } from "@/types";

import { DetailsView } from "./DetailsView";
import { renameEntry } from "./entryActions";
import {
  type DialogState,
  type ExplorerItem,
  type SortDir,
  type SortKey,
  type ViewMode,
  extOf,
} from "./explorerItem";
import { GridView } from "./GridView";
import { useFolderDocuments } from "./useFolderDocuments";

interface FolderContentsProps {
  /** The selected folder whose contents are shown (null = nothing selected). */
  folder: Folder | null;
  folders: Folder[];
  onOpenFolder: (id: string) => void;
  onChanged: () => void;
}

export function FolderContents({ folder, folders, onOpenFolder, onChanged }: FolderContentsProps) {
  const router = useRouter();
  const { docs, loading, error, reload: reloadDocs } = useFolderDocuments(folder?.id ?? null);
  const [view, setView] = useState<ViewMode>("details");
  const [sortKey, setSortKey] = useState<SortKey>("name");
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const [dialog, setDialog] = useState<DialogState>(null);

  // Reload this browser's contents AND notify the parent (folder tree / counts).
  const reload = useCallback(() => {
    void reloadDocs();
    onChanged();
  }, [reloadDocs, onChanged]);

  const subfolders = useMemo(
    () => (folder ? folders.filter((f) => f.parent_id === folder.id) : []),
    [folder, folders],
  );

  const items = useMemo<ExplorerItem[]>(() => {
    const folderItems: ExplorerItem[] = subfolders.map((f) => ({
      id: f.id,
      kind: "folder",
      name: f.name,
      type: "Folder",
      modified: f.created_at,
      size: null,
      folder: f,
    }));
    const docItems: ExplorerItem[] = docs.map((d) => ({
      id: d.id,
      kind: "doc",
      name: d.title,
      type: extOf(d.title),
      modified: d.created_at,
      size: d.size_bytes,
      status: d.processing_status,
      doc: d,
    }));

    const cmp = (a: ExplorerItem, b: ExplorerItem): number => {
      let r = 0;
      if (sortKey === "name") r = a.name.localeCompare(b.name);
      else if (sortKey === "type") r = a.type.localeCompare(b.type);
      else if (sortKey === "size") r = (a.size ?? -1) - (b.size ?? -1);
      else r = (a.modified ?? "").localeCompare(b.modified ?? "");
      return sortDir === "asc" ? r : -r;
    };
    // Folders always group before files (Explorer convention), sorted within.
    return [...folderItems.sort(cmp), ...docItems.sort(cmp)];
  }, [subfolders, docs, sortKey, sortDir]);

  const openItem = (item: ExplorerItem) => {
    if (item.kind === "folder") onOpenFolder(item.id);
    else router.push(`/documents/${item.id}`);
  };

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setSortKey(key);
      setSortDir("asc");
    }
  };

  // Background menu for right-clicking the empty whitespace of the browser.
  // Row/cell menus call stopPropagation, so this only fires on empty space and
  // offers folder-level actions on the currently open folder.
  const bgMenu = useRowMenu(
    folder
      ? [
          {
            label: "New subfolder",
            icon: <FolderPlus className="h-4 w-4" />,
            onSelect: () => setDialog({ type: "subfolder", folder }),
          },
          {
            label: "New Markdown file",
            icon: <FilePlus2 className="h-4 w-4" />,
            onSelect: () => setDialog({ type: "newMarkdown", folder }),
          },
          {
            label: "Upload document here",
            icon: <Upload className="h-4 w-4" />,
            onSelect: () => setDialog({ type: "upload", folder }),
          },
          {
            label: "Rename",
            icon: <TextCursorInput className="h-4 w-4" />,
            onSelect: () => void renameEntry("folder", folder.id, folder.name, reload),
          },
          {
            label: "Properties",
            icon: <Settings2 className="h-4 w-4" />,
            onSelect: () => setDialog({ type: "folderProps", folder }),
          },
        ]
      : [],
  );

  if (!folder) {
    return (
      <div className="flex h-full items-center justify-center p-8 text-sm text-muted-foreground">
        Select a folder to see its contents.
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      {/* Toolbar */}
      <div className="flex items-center justify-between gap-2 border-b px-3 py-2">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium">{folder.name}</div>
          <div className="text-xs text-muted-foreground">
            {subfolders.length} folder{subfolders.length === 1 ? "" : "s"} · {docs.length} document
            {docs.length === 1 ? "" : "s"}
          </div>
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            aria-label="New subfolder"
            title="New subfolder"
            onClick={() => setDialog({ type: "subfolder", folder })}
            className="rounded p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"
          >
            <FolderPlus className="h-4 w-4" />
          </button>
          <button
            type="button"
            aria-label="New Markdown file"
            title="New Markdown file"
            onClick={() => setDialog({ type: "newMarkdown", folder })}
            className="rounded p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"
          >
            <FilePlus2 className="h-4 w-4" />
          </button>
          <button
            type="button"
            aria-label="Upload document"
            title="Upload document here"
            onClick={() => setDialog({ type: "upload", folder })}
            className="rounded p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"
          >
            <Upload className="h-4 w-4" />
          </button>
          <div className="mx-1 h-5 w-px bg-border" />
          <ViewButton active={view === "details"} onClick={() => setView("details")} title="Details">
            <ListIcon className="h-4 w-4" />
          </ViewButton>
          <ViewButton active={view === "list"} onClick={() => setView("list")} title="List">
            <Rows3 className="h-4 w-4" />
          </ViewButton>
          <ViewButton active={view === "small"} onClick={() => setView("small")} title="Small icons">
            <LayoutGrid className="h-3.5 w-3.5" />
          </ViewButton>
          <ViewButton active={view === "large"} onClick={() => setView("large")} title="Large icons">
            <LayoutGrid className="h-5 w-5" />
          </ViewButton>
        </div>
      </div>

      {/* Sort control for non-details views */}
      {view !== "details" ? (
        <div className="flex items-center gap-2 border-b px-3 py-1.5 text-xs text-muted-foreground">
          <span>Sort:</span>
          {(["name", "type", "modified", "size"] as SortKey[]).map((k) => (
            <button
              key={k}
              type="button"
              onClick={() => toggleSort(k)}
              className={cn("rounded px-1.5 py-0.5 capitalize hover:bg-accent", sortKey === k && "text-foreground")}
            >
              {k}
              {sortKey === k ? (sortDir === "asc" ? " ▲" : " ▼") : ""}
            </button>
          ))}
        </div>
      ) : null}

      {/* Contents — each view owns its own virtualized scroll region so a
          folder with thousands of items renders only the visible rows.
          Right-clicking the whitespace here opens the folder background menu. */}
      <div className="flex min-h-0 flex-1 flex-col" onContextMenu={bgMenu.open}>
        {bgMenu.menu}
        {error ? (
          <div className="m-3 shrink-0 rounded border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {error}
          </div>
        ) : null}
        <div className="min-h-0 flex-1">
          {loading ? (
            <div className="p-4 text-sm text-muted-foreground">Loading…</div>
          ) : items.length === 0 ? (
            <div className="p-8 text-center text-sm text-muted-foreground">This folder is empty.</div>
          ) : view === "details" ? (
            <DetailsView items={items} sortKey={sortKey} sortDir={sortDir} onSort={toggleSort} onOpen={openItem} onAction={setDialog} onReload={reload} />
          ) : (
            <GridView items={items} view={view} onOpen={openItem} onAction={setDialog} onReload={reload} />
          )}
        </div>
      </div>

      {/* Hoisted dialogs */}
      {dialog?.type === "subfolder" ? (
        <FolderCreate open folders={folders} initialParentId={dialog.folder.id} onClose={() => setDialog(null)} onCreated={reload} />
      ) : null}
      {dialog?.type === "upload" ? (
        <DocumentUpload open defaultFolderId={dialog.folder.id} onClose={() => setDialog(null)} onCreated={reload} />
      ) : null}
      {dialog?.type === "newMarkdown" ? (
        <MarkdownEditor open defaultFolderId={dialog.folder.id} onClose={() => setDialog(null)} onSaved={reload} />
      ) : null}
      {dialog?.type === "editDoc" ? (
        <MarkdownEditor
          open
          documentId={dialog.doc.id}
          initialTitle={dialog.doc.title}
          onClose={() => setDialog(null)}
          onSaved={reload}
        />
      ) : null}
      {dialog?.type === "folderProps" ? (
        <FolderProperties folder={dialog.folder} open onClose={() => setDialog(null)} onSaved={reload} />
      ) : null}
      {dialog?.type === "docProps" ? (
        <DocumentProperties doc={dialog.doc} folders={folders} open onClose={() => setDialog(null)} onSaved={reload} />
      ) : null}
    </div>
  );
}

function ViewButton({
  active,
  onClick,
  title,
  children,
}: {
  active: boolean;
  onClick: () => void;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      title={title}
      aria-label={title}
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "flex h-7 w-7 items-center justify-center rounded",
        active ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent",
      )}
    >
      {children}
    </button>
  );
}
