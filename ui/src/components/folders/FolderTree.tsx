"use client";

import {
  ChevronDown,
  ChevronRight,
  Folder as FolderIcon,
  FolderPlus,
  MoreVertical,
  Settings2,
  Trash2,
  Upload,
} from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { List, type RowComponentProps } from "react-window";

import { type MenuItem, useRowMenu } from "@/components/common/ActionMenu";
import { DocumentUpload } from "@/components/documents/DocumentUpload";
import { FolderCreate } from "@/components/folders/FolderCreate";
import { FolderProperties } from "@/components/folders/FolderProperties";
import { cn } from "@/lib/utils";
import type { Folder } from "@/types";

import { deleteEntry } from "./entryActions";

interface FolderTreeProps {
  folders: Folder[];
  onMove: (folderId: string, newParentId: string | null) => void | Promise<void>;
  /** Reload after a rename / permission change / delete / create / upload. */
  onChanged: () => void;
  /** Currently-selected folder (highlighted). */
  selectedId?: string | null;
  /** Called when a folder name is clicked — selects it (no navigation). */
  onSelect?: (id: string) => void;
}

/** A folder flattened into the visible (expanded) list, with its tree depth. */
interface FlatNode {
  folder: Folder;
  depth: number;
  hasChildren: boolean;
  expanded: boolean;
}

const ROW_HEIGHT = 34;
const MAX_LIST_HEIGHT = 640;

/**
 * Build the ordered list of currently-visible nodes (root → expanded children),
 * so the virtualizer renders only what's on screen — this scales to thousands
 * of folders without mounting them all.
 */
function flatten(folders: Folder[], expanded: Set<string>): FlatNode[] {
  const childrenOf = new Map<string | null, Folder[]>();
  const visibleIds = new Set(folders.map((f) => f.id));
  for (const f of folders) {
    // Treat a folder whose parent isn't visible as a root (permission gaps).
    const key = f.parent_id && visibleIds.has(f.parent_id) ? f.parent_id : null;
    const bucket = childrenOf.get(key) ?? [];
    bucket.push(f);
    childrenOf.set(key, bucket);
  }
  for (const bucket of childrenOf.values()) {
    bucket.sort((a, b) => a.name.localeCompare(b.name));
  }

  const out: FlatNode[] = [];
  const walk = (parentKey: string | null, depth: number) => {
    for (const folder of childrenOf.get(parentKey) ?? []) {
      const hasChildren = (childrenOf.get(folder.id) ?? []).length > 0;
      const isExpanded = expanded.has(folder.id);
      out.push({ folder, depth, hasChildren, expanded: isExpanded });
      if (hasChildren && isExpanded) walk(folder.id, depth + 1);
    }
  };
  walk(null, 0);
  return out;
}

type DialogState =
  | { type: "properties"; folder: Folder }
  | { type: "subfolder"; folder: Folder }
  | { type: "upload"; folder: Folder }
  | null;

export function FolderTree({ folders, onMove, onChanged, selectedId, onSelect }: FolderTreeProps) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [dragOverId, setDragOverId] = useState<string | "__root__" | null>(null);
  // Dialogs are hoisted here (not per-row) so they survive rows unmounting as
  // the virtualized list scrolls.
  const [dialog, setDialog] = useState<DialogState>(null);

  const visible = useMemo(() => flatten(folders, expanded), [folders, expanded]);

  const toggle = (id: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const isDescendantOf = (candidateId: string, ancestorId: string): boolean => {
    const ancestor = folders.find((f) => f.id === ancestorId);
    const candidate = folders.find((f) => f.id === candidateId);
    if (!ancestor || !candidate) return false;
    const prefix = ancestor.dot_path;
    return candidate.dot_path === prefix || candidate.dot_path.startsWith(`${prefix}.`);
  };

  const handleDrop = async (draggedId: string, targetId: string | null) => {
    if (!draggedId || draggedId === targetId) return;
    if (targetId !== null && isDescendantOf(targetId, draggedId)) return;
    await onMove(draggedId, targetId);
  };

  // DOM onDrop can't await, so run the (async) move fire-and-forget with a
  // defensive .catch — onMove already try/catches, this guards against any
  // future throw becoming an unhandled rejection.
  const handleDropSafe = (draggedId: string, targetId: string | null) => {
    void handleDrop(draggedId, targetId).catch(() => {});
  };

  if (visible.length === 0) {
    return <p className="p-4 text-sm text-muted-foreground">No folders yet.</p>;
  }

  const listHeight = Math.min(visible.length * ROW_HEIGHT, MAX_LIST_HEIGHT) + 8;

  return (
    <>
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragOverId("__root__");
        }}
        onDragLeave={() => setDragOverId(null)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOverId(null);
          handleDropSafe(e.dataTransfer.getData("application/x-folder-id"), null);
        }}
        className={cn("rounded-md p-1", dragOverId === "__root__" && "ring-2 ring-primary ring-inset")}
      >
        <List
          rowComponent={FolderRowComponent}
          rowCount={visible.length}
          rowHeight={ROW_HEIGHT}
          rowProps={{
            visible,
            dragOverId,
            selectedId: selectedId ?? null,
            onSelect: onSelect ?? null,
            onToggle: toggle,
            onDragOverRow: setDragOverId,
            onDropRow: handleDropSafe,
            onAction: setDialog,
            onDelete: (folder: Folder) => void deleteEntry("folder", folder.id, folder.name, onChanged),
          }}
          style={{ height: listHeight, width: "100%" }}
        />
      </div>

      {dialog?.type === "properties" ? (
        <FolderProperties
          folder={dialog.folder}
          open
          onClose={() => setDialog(null)}
          onSaved={onChanged}
        />
      ) : null}
      {dialog?.type === "subfolder" ? (
        <FolderCreate
          open
          folders={folders}
          initialParentId={dialog.folder.id}
          onClose={() => setDialog(null)}
          onCreated={onChanged}
        />
      ) : null}
      {dialog?.type === "upload" ? (
        <DocumentUpload
          open
          defaultFolderId={dialog.folder.id}
          onClose={() => setDialog(null)}
          onCreated={onChanged}
        />
      ) : null}
    </>
  );
}

interface FolderRowProps {
  visible: FlatNode[];
  dragOverId: string | "__root__" | null;
  selectedId: string | null;
  onSelect: ((id: string) => void) | null;
  onToggle: (id: string) => void;
  onDragOverRow: (id: string | null) => void;
  onDropRow: (draggedId: string, targetId: string | null) => void;
  onAction: (d: DialogState) => void;
  onDelete: (folder: Folder) => void;
}

function FolderRowComponent({
  index,
  style,
  visible,
  dragOverId,
  selectedId,
  onSelect,
  onToggle,
  onDragOverRow,
  onDropRow,
  onAction,
  onDelete,
}: RowComponentProps<FolderRowProps>) {
  const node = visible[index]!;
  const { folder, depth, hasChildren, expanded } = node;
  const isDragTarget = dragOverId === folder.id;
  const isSelected = selectedId === folder.id;

  const items: MenuItem[] = [
    {
      label: "New subfolder",
      icon: <FolderPlus className="h-4 w-4" />,
      onSelect: () => onAction({ type: "subfolder", folder }),
    },
    {
      label: "Upload document here",
      icon: <Upload className="h-4 w-4" />,
      onSelect: () => onAction({ type: "upload", folder }),
    },
    {
      label: "Properties",
      icon: <Settings2 className="h-4 w-4" />,
      onSelect: () => onAction({ type: "properties", folder }),
    },
    {
      label: "Delete",
      icon: <Trash2 className="h-4 w-4" />,
      destructive: true,
      onSelect: () => onDelete(folder),
    },
  ];
  const { open, menu } = useRowMenu(items);

  return (
    <div style={style}>
      <div
        draggable
        onContextMenu={open}
        onDragStart={(e) => {
          e.dataTransfer.setData("application/x-folder-id", folder.id);
          e.dataTransfer.effectAllowed = "move";
        }}
        onDragOver={(e) => {
          e.preventDefault();
          e.stopPropagation();
          e.dataTransfer.dropEffect = "move";
          onDragOverRow(folder.id);
        }}
        onDrop={(e) => {
          e.preventDefault();
          e.stopPropagation();
          onDropRow(e.dataTransfer.getData("application/x-folder-id"), folder.id);
        }}
        style={{ paddingLeft: `${depth * 1.25 + 0.5}rem` }}
        className={cn(
          "group flex h-8 cursor-grab items-center gap-1.5 rounded-md pr-2 text-sm transition-colors",
          isDragTarget
            ? "bg-accent ring-2 ring-primary"
            : isSelected
              ? "bg-accent"
              : "hover:bg-accent/60",
        )}
      >
        {hasChildren ? (
          <button
            type="button"
            onClick={() => onToggle(folder.id)}
            aria-label={expanded ? "Collapse" : "Expand"}
            className="flex h-5 w-5 shrink-0 items-center justify-center rounded hover:bg-accent"
          >
            {expanded ? (
              <ChevronDown className="h-3.5 w-3.5" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5" />
            )}
          </button>
        ) : (
          <span className="inline-block h-5 w-5 shrink-0" aria-hidden />
        )}
        <FolderIcon className="h-4 w-4 shrink-0 text-muted-foreground" />
        {onSelect ? (
          <button
            type="button"
            draggable={false}
            onClick={(e) => {
              e.stopPropagation();
              onSelect(folder.id);
            }}
            className="truncate text-left font-medium hover:underline"
          >
            {folder.name}
          </button>
        ) : (
          <Link
            href={`/folders/${folder.id}`}
            draggable={false}
            onClick={(e) => e.stopPropagation()}
            className="truncate font-medium hover:underline"
          >
            {folder.name}
          </Link>
        )}
        <button
          type="button"
          aria-label="Folder actions"
          onClick={open}
          className="ml-auto shrink-0 rounded p-1 text-muted-foreground opacity-0 hover:bg-accent hover:text-foreground focus:opacity-100 group-hover:opacity-100"
        >
          <MoreVertical className="h-4 w-4" />
        </button>
      </div>
      {menu}
    </div>
  );
}
