"use client";

import {
  ArrowDown,
  ArrowUp,
  FilePlus2,
  FileText,
  Folder as FolderIcon,
  FolderPlus,
  LayoutGrid,
  List as ListIcon,
  Loader2,
  Pencil,
  Rows3,
  Settings2,
  SquareArrowOutUpRight,
  TextCursorInput,
  Trash2,
  Upload,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  type CellComponentProps,
  Grid,
  List,
  type RowComponentProps,
} from "react-window";

import { type MenuItem, useRowMenu } from "@/components/common/ActionMenu";
import { DocumentProperties } from "@/components/documents/DocumentProperties";
import { DocumentUpload } from "@/components/documents/DocumentUpload";
import { MarkdownEditor } from "@/components/documents/MarkdownEditor";
import { FolderCreate } from "@/components/folders/FolderCreate";
import { FolderProperties } from "@/components/folders/FolderProperties";
import { Badge } from "@/components/ui/badge";
import { deleteDocument, listDocuments, updateDocument } from "@/lib/api/documents";
import { deleteFolder, updateFolder } from "@/lib/api/folders";
import { formatBytes, formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Document, Folder } from "@/types";

type ViewMode = "details" | "list" | "large" | "small";
type SortKey = "name" | "modified" | "size" | "type";
type SortDir = "asc" | "desc";

// Fixed row/cell metrics for the virtualizer (react-window needs known sizes).
const DETAILS_ROW_H = 32;
const LIST_ROW_H = 34;
const SMALL_CELL_H = 92;
const LARGE_CELL_H = 116;
// Minimum cell width per grid view, used to derive the responsive column count.
const MIN_CELL_W: Record<"list" | "small" | "large", number> = {
  list: 220,
  small: 104,
  large: 132,
};
// Shared column template so the details header and rows stay aligned.
const DETAILS_COLS = "minmax(120px,1fr) 150px 90px 170px 90px";

interface ExplorerItem {
  id: string;
  kind: "folder" | "doc";
  name: string;
  type: string;
  modified: string | null;
  size: number | null;
  /** Processing status for docs; undefined for folders. */
  status?: Document["processing_status"];
  folder?: Folder;
  doc?: Document;
}

/** True while a document is still being extracted/indexed. */
function isProcessing(status: Document["processing_status"]): boolean {
  return status === "PENDING" || status === "PROCESSING";
}

/** Compact status badge shown in the file browser; nothing once ready. */
function StatusBadge({ status }: { status: Document["processing_status"] }) {
  if (status === "SUCCESS") return null;
  if (status === "FAILED") return <Badge variant="destructive">Failed</Badge>;
  return (
    <Badge variant="secondary" className="gap-1">
      <Loader2 className="h-3 w-3 animate-spin" />
      Processing
    </Badge>
  );
}

type DialogState =
  | { type: "subfolder"; folder: Folder }
  | { type: "upload"; folder: Folder }
  | { type: "newMarkdown"; folder: Folder }
  | { type: "editDoc"; doc: Document }
  | { type: "folderProps"; folder: Folder }
  | { type: "docProps"; doc: Document }
  | null;

function extOf(title: string): string {
  const m = /\.([a-z0-9]+)$/i.exec(title.trim());
  return m ? m[1]!.toUpperCase() : "Document";
}

// Text originals we can round-trip through the Markdown editor. Detection is by
// extension (the list has no body); authored docs get a `.md` name on create,
// so they surface "Edit" too. Non-text originals (pdf/docx) are read-only here.
const _MARKDOWN_EDITABLE_EXTS = new Set(["md", "markdown", "txt"]);
function isMarkdownEditable(title: string): boolean {
  const m = /\.([a-z0-9]+)$/i.exec(title.trim());
  return m ? _MARKDOWN_EDITABLE_EXTS.has(m[1]!.toLowerCase()) : false;
}

/**
 * Prompt for a new name and rename a folder or document in place. Uses the same
 * lightweight browser dialogs as Delete's confirm(); a null/blank/unchanged name
 * is a no-op. Folder names cannot contain "." (matches FolderProperties' rule).
 */
async function renameEntry(
  kind: "folder" | "doc",
  id: string,
  currentName: string,
  onDone: () => void,
): Promise<void> {
  const next = window.prompt(`Rename "${currentName}" to:`, currentName);
  if (next == null) return;
  const trimmed = next.trim();
  if (!trimmed || trimmed === currentName) return;
  if (kind === "folder" && trimmed.includes(".")) {
    window.alert("Folder names cannot contain '.'");
    return;
  }
  try {
    if (kind === "folder") await updateFolder(id, { name: trimmed });
    else await updateDocument(id, { title: trimmed });
    onDone();
  } catch (e) {
    window.alert(e instanceof Error ? e.message : "Rename failed");
  }
}

/** Track a container's pixel size so the virtualizer can fill it exactly. */
function useElementSize() {
  const ref = useRef<HTMLDivElement | null>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => {
      const rect = entry?.contentRect;
      if (rect) setSize({ width: rect.width, height: rect.height });
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return { ref, ...size };
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
  const [error, setError] = useState<string | null>(null);

  // `silent` refreshes (used by polling) skip the loading spinner and keep the
  // current rows on error so the list doesn't flicker or blank out.
  const loadDocs = useCallback(
    async (silent = false) => {
      if (!folder) {
        setDocs([]);
        return;
      }
      if (!silent) setLoading(true);
      try {
        // The API caps page_size at 200 (PaginationParams), so pull every page
        // and accumulate — the list is virtualized, so holding all rows in
        // memory is cheap and the user can scroll the whole folder.
        const pageSize = 200;
        const first = await listDocuments(1, pageSize, folder.id);
        const all = [...first.items];
        const totalPages = Math.max(1, Math.ceil(first.total / pageSize));
        for (let page = 2; page <= totalPages; page += 1) {
          const next = await listDocuments(page, pageSize, folder.id);
          if (next.items.length === 0) break; // safety against a shifting total
          all.push(...next.items);
        }
        setDocs(all);
        setError(null);
      } catch (e) {
        // Surface the failure instead of masking it as an empty folder.
        if (!silent) {
          setDocs([]);
          setError(e instanceof Error ? e.message : "Failed to load documents");
        }
      } finally {
        if (!silent) setLoading(false);
      }
    },
    [folder],
  );

  useEffect(() => {
    void loadDocs();
  }, [loadDocs]);

  // While any document is still processing, poll quietly so its status flips to
  // ready (or failed) without a manual refresh. Stops once nothing is pending.
  const anyProcessing = useMemo(() => docs.some((d) => isProcessing(d.processing_status)), [docs]);
  useEffect(() => {
    if (!anyProcessing) return;
    const timer = setInterval(() => void loadDocs(true), 4000);
    return () => clearInterval(timer);
  }, [anyProcessing, loadDocs]);

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
            <DetailsView items={items} sortKey={sortKey} sortDir={sortDir} onSort={toggleSort} onOpen={openItem} onAction={setDialog} onDelete={reload} />
          ) : (
            <GridView items={items} view={view} onOpen={openItem} onAction={setDialog} onDelete={reload} />
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
            label: "New Markdown file",
            icon: <FilePlus2 className="h-4 w-4" />,
            onSelect: () => onAction({ type: "newMarkdown", folder: item.folder! }),
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
          ...(item.doc && isMarkdownEditable(item.name)
            ? [
                {
                  label: "Edit",
                  icon: <Pencil className="h-4 w-4" />,
                  onSelect: () => onAction({ type: "editDoc", doc: item.doc! }),
                },
              ]
            : []),
          {
            label: "Properties",
            icon: <Settings2 className="h-4 w-4" />,
            onSelect: () => onAction({ type: "docProps", doc: item.doc! }),
          },
        ]),
    {
      label: "Rename",
      icon: <TextCursorInput className="h-4 w-4" />,
      onSelect: () => void renameEntry(item.kind, item.id, item.name, onDelete),
    },
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
  const { ref, height } = useElementSize();
  const header = (key: SortKey, label: string, alignRight?: boolean) => (
    <button
      type="button"
      onClick={() => onSort(key)}
      className={cn(
        "flex items-center gap-1 px-3 py-1.5 font-medium hover:text-foreground",
        alignRight ? "justify-end" : "text-left",
      )}
    >
      {label}
      {sortKey === key ? (
        sortDir === "asc" ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />
      ) : null}
    </button>
  );
  return (
    <div className="flex h-full flex-col text-sm">
      {/* Fixed header (the rows below scroll under it). */}
      <div
        className="grid shrink-0 items-center border-b bg-background text-xs text-muted-foreground"
        style={{ gridTemplateColumns: DETAILS_COLS }}
      >
        {header("name", "Name")}
        <div className="px-3 py-1.5 font-medium">Status</div>
        {header("type", "Type")}
        {header("modified", "Modified")}
        {header("size", "Size", true)}
      </div>
      <div ref={ref} className="min-h-0 flex-1">
        <List
          rowComponent={DetailsRow}
          rowCount={items.length}
          rowHeight={DETAILS_ROW_H}
          rowProps={{ items, onOpen, onAction, onDelete }}
          style={{ height, width: "100%" }}
        />
      </div>
    </div>
  );
}

function DetailsRow({
  index,
  style,
  ariaAttributes,
  items,
  onOpen,
  onAction,
  onDelete,
}: RowComponentProps<RowProps>) {
  const item = items[index]!;
  const { open, menu } = useItemMenu(item, onOpen, onAction, onDelete);
  return (
    <div
      {...ariaAttributes}
      style={{ ...style, gridTemplateColumns: DETAILS_COLS }}
      onDoubleClick={() => onOpen(item)}
      onContextMenu={open}
      className="grid cursor-default items-center border-b border-border/50 text-sm hover:bg-accent/50"
    >
      <span className="flex min-w-0 items-center gap-2 px-3">
        <ItemIcon item={item} className="h-4 w-4 shrink-0" />
        <span className="truncate">{item.name}</span>
        {menu}
      </span>
      <span className="px-3">{item.status ? <StatusBadge status={item.status} /> : null}</span>
      <span className="truncate px-3 text-muted-foreground">{item.type}</span>
      <span className="truncate px-3 text-muted-foreground">
        {item.modified ? formatDate(item.modified) : "—"}
      </span>
      <span className="px-3 text-right text-muted-foreground">
        {item.size != null ? formatBytes(item.size) : "—"}
      </span>
    </div>
  );
}

interface CellProps extends RowProps {
  columnCount: number;
  view: ViewMode;
}

/** Virtualized grid for the list / small-icon / large-icon views. */
function GridView({ items, view, onOpen, onAction, onDelete }: RowProps & { view: ViewMode }) {
  const { ref, width, height } = useElementSize();
  const kind = view as "list" | "small" | "large";
  const minCol = MIN_CELL_W[kind];
  const rowHeight = view === "list" ? LIST_ROW_H : view === "large" ? LARGE_CELL_H : SMALL_CELL_H;
  // Fit as many min-width columns as the measured width allows (≥ 1).
  const columnCount = Math.max(1, Math.floor((width || minCol) / minCol));
  const rowCount = Math.ceil(items.length / columnCount);
  return (
    <div ref={ref} className="h-full">
      <Grid
        cellComponent={GridCell}
        cellProps={{ items, columnCount, view, onOpen, onAction, onDelete }}
        columnCount={columnCount}
        columnWidth={`${100 / columnCount}%`}
        rowCount={rowCount}
        rowHeight={rowHeight}
        style={{ height, width: "100%" }}
      />
    </div>
  );
}

function GridCell({
  rowIndex,
  columnIndex,
  style,
  items,
  columnCount,
  view,
  onOpen,
  onAction,
  onDelete,
}: CellComponentProps<CellProps>) {
  const item = items[rowIndex * columnCount + columnIndex];
  if (!item) return <div style={style} />; // trailing slot on the last row
  return (
    <div style={style} className="p-1">
      <IconCell item={item} view={view} onOpen={onOpen} onAction={onAction} onDelete={onDelete} />
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
        className="flex h-full w-full items-center gap-2 rounded px-2 text-left text-sm hover:bg-accent/50"
      >
        <ItemIcon item={item} className="h-4 w-4 shrink-0" />
        <span className="truncate">{item.name}</span>
        {item.status ? <StatusBadge status={item.status} /> : null}
        {menu}
      </button>
    );
  }
  return (
    <button
      type="button"
      onDoubleClick={() => onOpen(item)}
      onContextMenu={open}
      className="flex h-full w-full flex-col items-center gap-1 rounded p-2 text-center hover:bg-accent/50"
    >
      <ItemIcon item={item} className={large ? "h-12 w-12" : "h-8 w-8"} />
      <span className={cn("w-full truncate", large ? "text-sm" : "text-xs")}>{item.name}</span>
      {item.status && item.status !== "SUCCESS" ? <StatusBadge status={item.status} /> : null}
      {menu}
    </button>
  );
}
