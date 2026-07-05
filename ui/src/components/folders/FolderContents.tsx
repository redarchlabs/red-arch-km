"use client";

import {
  ArrowDown,
  ArrowUp,
  FileText,
  Folder as FolderIcon,
  FolderPlus,
  LayoutGrid,
  List as ListIcon,
  Rows3,
  Settings2,
  SquareArrowOutUpRight,
  Trash2,
  Upload,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { type MenuItem, useRowMenu } from "@/components/common/ActionMenu";
import { DocumentProperties } from "@/components/documents/DocumentProperties";
import { DocumentUpload } from "@/components/documents/DocumentUpload";
import { FolderCreate } from "@/components/folders/FolderCreate";
import { FolderProperties } from "@/components/folders/FolderProperties";
import { deleteDocument, listDocuments } from "@/lib/api/documents";
import { deleteFolder } from "@/lib/api/folders";
import { formatBytes, formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Document, Folder } from "@/types";

type ViewMode = "details" | "list" | "large" | "small";
type SortKey = "name" | "modified" | "size" | "type";
type SortDir = "asc" | "desc";

interface ExplorerItem {
  id: string;
  kind: "folder" | "doc";
  name: string;
  type: string;
  modified: string | null;
  size: number | null;
  folder?: Folder;
  doc?: Document;
}

type DialogState =
  | { type: "subfolder"; folder: Folder }
  | { type: "upload"; folder: Folder }
  | { type: "folderProps"; folder: Folder }
  | { type: "docProps"; doc: Document }
  | null;

function extOf(title: string): string {
  const m = /\.([a-z0-9]+)$/i.exec(title.trim());
  return m ? m[1]!.toUpperCase() : "Document";
}

interface FolderContentsProps {
  /** The selected folder whose contents are shown (null = nothing selected). */
  folder: Folder | null;
  folders: Folder[];
  onOpenFolder: (id: string) => void;
  onChanged: () => void;
}

export function FolderContents({ folder, folders, onOpenFolder, onChanged }: FolderContentsProps) {
  const router = useRouter();
  const [docs, setDocs] = useState<Document[]>([]);
  const [loading, setLoading] = useState(false);
  const [view, setView] = useState<ViewMode>("details");
  const [sortKey, setSortKey] = useState<SortKey>("name");
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const [dialog, setDialog] = useState<DialogState>(null);

  const loadDocs = useCallback(async () => {
    if (!folder) {
      setDocs([]);
      return;
    }
    setLoading(true);
    try {
      const res = await listDocuments(1, 500, folder.id);
      setDocs(res.items);
    } catch {
      setDocs([]);
    } finally {
      setLoading(false);
    }
  }, [folder]);

  useEffect(() => {
    void loadDocs();
  }, [loadDocs]);

  const reload = useCallback(() => {
    void loadDocs();
    onChanged();
  }, [loadDocs, onChanged]);

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

      {/* Scrollable contents */}
      <div className="min-h-0 flex-1 overflow-y-auto">
        {loading ? (
          <div className="p-4 text-sm text-muted-foreground">Loading…</div>
        ) : items.length === 0 ? (
          <div className="p-8 text-center text-sm text-muted-foreground">This folder is empty.</div>
        ) : view === "details" ? (
          <DetailsView items={items} sortKey={sortKey} sortDir={sortDir} onSort={toggleSort} onOpen={openItem} onAction={setDialog} onDelete={reload} />
        ) : (
          <IconsView items={items} view={view} onOpen={openItem} onAction={setDialog} onDelete={reload} />
        )}
      </div>

      {/* Hoisted dialogs */}
      {dialog?.type === "subfolder" ? (
        <FolderCreate open folders={folders} initialParentId={dialog.folder.id} onClose={() => setDialog(null)} onCreated={reload} />
      ) : null}
      {dialog?.type === "upload" ? (
        <DocumentUpload open defaultFolderId={dialog.folder.id} onClose={() => setDialog(null)} onCreated={reload} />
      ) : null}
      {dialog?.type === "folderProps" ? (
        <FolderProperties folder={dialog.folder} open onClose={() => setDialog(null)} onSaved={reload} />
      ) : null}
      {dialog?.type === "docProps" ? (
        <DocumentProperties document={dialog.doc} folders={folders} open onClose={() => setDialog(null)} onSaved={reload} />
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

/** Build the shared context menu for an item. */
function useItemMenu(
  item: ExplorerItem,
  onOpen: (i: ExplorerItem) => void,
  onAction: (d: DialogState) => void,
  onDelete: () => void,
) {
  const items: MenuItem[] = [
    { label: "Open", icon: <SquareArrowOutUpRight className="h-4 w-4" />, onSelect: () => onOpen(item) },
    ...(item.kind === "folder" && item.folder
      ? [
          {
            label: "New subfolder",
            icon: <FolderPlus className="h-4 w-4" />,
            onSelect: () => onAction({ type: "subfolder", folder: item.folder! }),
          },
          {
            label: "Upload document here",
            icon: <Upload className="h-4 w-4" />,
            onSelect: () => onAction({ type: "upload", folder: item.folder! }),
          },
          {
            label: "Properties",
            icon: <Settings2 className="h-4 w-4" />,
            onSelect: () => onAction({ type: "folderProps", folder: item.folder! }),
          },
        ]
      : [
          {
            label: "Properties",
            icon: <Settings2 className="h-4 w-4" />,
            onSelect: () => onAction({ type: "docProps", doc: item.doc! }),
          },
        ]),
    {
      label: "Delete",
      icon: <Trash2 className="h-4 w-4" />,
      destructive: true,
      onSelect: async () => {
        if (!confirm(`Delete "${item.name}"?${item.kind === "folder" ? " Documents inside are not deleted." : ""}`))
          return;
        if (item.kind === "folder") await deleteFolder(item.id);
        else await deleteDocument(item.id);
        onDelete();
      },
    },
  ];
  return useRowMenu(items);
}

function ItemIcon({ item, className }: { item: ExplorerItem; className?: string }) {
  return item.kind === "folder" ? (
    <FolderIcon className={cn("text-muted-foreground", className)} />
  ) : (
    <FileText className={cn("text-muted-foreground", className)} />
  );
}

interface RowProps {
  items: ExplorerItem[];
  onOpen: (i: ExplorerItem) => void;
  onAction: (d: DialogState) => void;
  onDelete: () => void;
}

function DetailsView({
  items,
  sortKey,
  sortDir,
  onSort,
  onOpen,
  onAction,
  onDelete,
}: RowProps & { sortKey: SortKey; sortDir: SortDir; onSort: (k: SortKey) => void }) {
  const header = (key: SortKey, label: string, extra?: string) => (
    <button
      type="button"
      onClick={() => onSort(key)}
      className={cn("flex items-center gap-1 py-1 text-left font-medium hover:text-foreground", extra)}
    >
      {label}
      {sortKey === key ? (
        sortDir === "asc" ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />
      ) : null}
    </button>
  );
  return (
    <table className="w-full text-sm">
      <thead className="sticky top-0 border-b bg-background text-xs text-muted-foreground">
        <tr className="px-2">
          <th className="px-3 text-left font-medium">{header("name", "Name")}</th>
          <th className="px-3 text-left font-medium">{header("type", "Type")}</th>
          <th className="px-3 text-left font-medium">{header("modified", "Modified")}</th>
          <th className="px-3 text-right font-medium">{header("size", "Size", "ml-auto")}</th>
        </tr>
      </thead>
      <tbody>
        {items.map((item) => (
          <DetailsRow key={`${item.kind}:${item.id}`} item={item} onOpen={onOpen} onAction={onAction} onDelete={onDelete} />
        ))}
      </tbody>
    </table>
  );
}

function DetailsRow({ item, onOpen, onAction, onDelete }: { item: ExplorerItem } & Omit<RowProps, "items">) {
  const { open, menu } = useItemMenu(item, onOpen, onAction, onDelete);
  return (
    <tr
      onDoubleClick={() => onOpen(item)}
      onContextMenu={open}
      className="cursor-default border-b border-border/50 hover:bg-accent/50"
    >
      <td className="px-3 py-1.5">
        <span className="flex items-center gap-2">
          <ItemIcon item={item} className="h-4 w-4 shrink-0" />
          <span className="truncate">{item.name}</span>
          {menu}
        </span>
      </td>
      <td className="px-3 py-1.5 text-muted-foreground">{item.type}</td>
      <td className="px-3 py-1.5 text-muted-foreground">{item.modified ? formatDate(item.modified) : "—"}</td>
      <td className="px-3 py-1.5 text-right text-muted-foreground">
        {item.size != null ? formatBytes(item.size) : "—"}
      </td>
    </tr>
  );
}

function IconsView({ items, view, onOpen, onAction, onDelete }: RowProps & { view: ViewMode }) {
  const large = view === "large";
  return (
    <div
      className={cn(
        "grid gap-2 p-3",
        view === "list"
          ? "grid-cols-[repeat(auto-fill,minmax(200px,1fr))]"
          : large
            ? "grid-cols-[repeat(auto-fill,minmax(120px,1fr))]"
            : "grid-cols-[repeat(auto-fill,minmax(90px,1fr))]",
      )}
    >
      {items.map((item) => (
        <IconCell key={`${item.kind}:${item.id}`} item={item} view={view} onOpen={onOpen} onAction={onAction} onDelete={onDelete} />
      ))}
    </div>
  );
}

function IconCell({ item, view, onOpen, onAction, onDelete }: { item: ExplorerItem; view: ViewMode } & Omit<RowProps, "items">) {
  const { open, menu } = useItemMenu(item, onOpen, onAction, onDelete);
  const large = view === "large";
  if (view === "list") {
    return (
      <button
        type="button"
        onDoubleClick={() => onOpen(item)}
        onContextMenu={open}
        className="flex items-center gap-2 rounded px-2 py-1 text-left text-sm hover:bg-accent/50"
      >
        <ItemIcon item={item} className="h-4 w-4 shrink-0" />
        <span className="truncate">{item.name}</span>
        {menu}
      </button>
    );
  }
  return (
    <button
      type="button"
      onDoubleClick={() => onOpen(item)}
      onContextMenu={open}
      className="flex flex-col items-center gap-1 rounded p-2 text-center hover:bg-accent/50"
    >
      <ItemIcon item={item} className={large ? "h-12 w-12" : "h-8 w-8"} />
      <span className={cn("w-full truncate", large ? "text-sm" : "text-xs")}>{item.name}</span>
      {menu}
    </button>
  );
}
