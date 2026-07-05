"use client";

import {
  ChevronDown,
  ChevronRight,
  Folder as FolderIcon,
  MoreVertical,
  Settings2,
  Trash2,
} from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import { type MenuItem, useRowMenu } from "@/components/common/ActionMenu";
import { FolderProperties } from "@/components/folders/FolderProperties";
import { deleteFolder } from "@/lib/api/folders";
import { cn } from "@/lib/utils";
import type { Folder } from "@/types";

interface FolderTreeProps {
  folders: Folder[];
  onMove: (folderId: string, newParentId: string | null) => void | Promise<void>;
  /** Reload folders after a rename / permission change / delete. */
  onChanged: () => void;
}

interface TreeNode {
  folder: Folder;
  children: TreeNode[];
}

function buildTree(folders: Folder[]): TreeNode[] {
  const byParent = new Map<string | null, TreeNode[]>();
  const nodes: Record<string, TreeNode> = {};

  for (const folder of folders) {
    nodes[folder.id] = { folder, children: [] };
  }

  for (const folder of folders) {
    const key = folder.parent_id ?? null;
    const bucket = byParent.get(key) ?? [];
    bucket.push(nodes[folder.id]!);
    byParent.set(key, bucket);
  }

  for (const node of Object.values(nodes)) {
    node.children = (byParent.get(node.folder.id) ?? []).sort((a, b) =>
      a.folder.name.localeCompare(b.folder.name),
    );
  }

  // Root = folders whose parent is null OR whose parent isn't in the visible set
  // (fallback handles the case where a user can see a folder but not its ancestor).
  const visibleIds = new Set(folders.map((f) => f.id));
  return folders
    .filter((f) => f.parent_id === null || !visibleIds.has(f.parent_id))
    .sort((a, b) => a.name.localeCompare(b.name))
    .map((f) => nodes[f.id]!);
}

export function FolderTree({ folders, onMove, onChanged }: FolderTreeProps) {
  const tree = useMemo(() => buildTree(folders), [folders]);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [dragOverId, setDragOverId] = useState<string | "__root__" | null>(null);

  /**
   * Fast descendant check using the server-maintained dot_path prefix.
   * Moving folder A under any node whose dot_path is "A" or starts with
   * "A." would create a cycle; the server rejects this, but we also
   * short-circuit here so the user gets immediate feedback instead of
   * a round-trip failure.
   */
  const isDescendantOf = (candidateId: string, ancestorId: string): boolean => {
    const ancestor = folders.find((f) => f.id === ancestorId);
    const candidate = folders.find((f) => f.id === candidateId);
    if (!ancestor || !candidate) return false;
    const prefix = ancestor.dot_path;
    return (
      candidate.dot_path === prefix || candidate.dot_path.startsWith(`${prefix}.`)
    );
  };

  const toggle = (id: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const handleDragStart = (e: React.DragEvent, folderId: string) => {
    e.dataTransfer.setData("application/x-folder-id", folderId);
    e.dataTransfer.effectAllowed = "move";
  };

  const handleDragOver = (e: React.DragEvent, targetId: string | "__root__") => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    setDragOverId(targetId);
  };

  const handleDragLeave = () => setDragOverId(null);

  const handleDrop = async (e: React.DragEvent, targetId: string | null) => {
    e.preventDefault();
    setDragOverId(null);
    const draggedId = e.dataTransfer.getData("application/x-folder-id");
    if (!draggedId || draggedId === targetId) return;
    // Short-circuit cycle attempts client-side. The server also rejects.
    if (targetId !== null && isDescendantOf(targetId, draggedId)) return;
    await onMove(draggedId, targetId);
  };

  if (tree.length === 0) {
    return <p className="p-4 text-sm text-muted-foreground">No folders yet.</p>;
  }

  return (
    <div
      onDragOver={(e) => handleDragOver(e, "__root__")}
      onDragLeave={handleDragLeave}
      onDrop={(e) => handleDrop(e, null)}
      className={cn(
        "rounded-md",
        dragOverId === "__root__" && "ring-2 ring-primary ring-offset-2",
      )}
    >
      <ul className="space-y-0.5 p-2">
        {tree.map((node) => (
          <TreeRow
            key={node.folder.id}
            node={node}
            depth={0}
            folders={folders}
            expanded={expanded}
            dragOverId={dragOverId}
            onToggle={toggle}
            onChanged={onChanged}
            onDragStart={handleDragStart}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
          />
        ))}
      </ul>
    </div>
  );
}

interface TreeRowProps {
  node: TreeNode;
  depth: number;
  folders: Folder[];
  expanded: Set<string>;
  dragOverId: string | "__root__" | null;
  onToggle: (id: string) => void;
  onChanged: () => void;
  onDragStart: (e: React.DragEvent, folderId: string) => void;
  onDragOver: (e: React.DragEvent, targetId: string | "__root__") => void;
  onDragLeave: () => void;
  onDrop: (e: React.DragEvent, targetId: string | null) => void;
}

function TreeRow({
  node,
  depth,
  folders,
  expanded,
  dragOverId,
  onToggle,
  onChanged,
  onDragStart,
  onDragOver,
  onDragLeave,
  onDrop,
}: TreeRowProps) {
  const { folder, children } = node;
  const isExpanded = expanded.has(folder.id);
  const hasChildren = children.length > 0;
  const isDragTarget = dragOverId === folder.id;
  const [propsOpen, setPropsOpen] = useState(false);

  const items: MenuItem[] = [
    {
      label: "Properties",
      icon: <Settings2 className="h-4 w-4" />,
      onSelect: () => setPropsOpen(true),
    },
    {
      label: "Delete",
      icon: <Trash2 className="h-4 w-4" />,
      destructive: true,
      onSelect: async () => {
        if (!confirm(`Delete folder "${folder.name}"? Documents inside are not deleted.`)) return;
        await deleteFolder(folder.id);
        onChanged();
      },
    },
  ];
  const { open, menu } = useRowMenu(items);

  return (
    <li>
      <div
        draggable
        onContextMenu={open}
        onDragStart={(e) => onDragStart(e, folder.id)}
        onDragOver={(e) => {
          e.stopPropagation();
          onDragOver(e, folder.id);
        }}
        onDragLeave={onDragLeave}
        onDrop={(e) => {
          e.stopPropagation();
          onDrop(e, folder.id);
        }}
        style={{ paddingLeft: `${depth * 1.5 + 0.5}rem` }}
        className={cn(
          "group flex cursor-grab items-center gap-2 rounded-md py-1.5 pr-2 text-sm transition-colors",
          isDragTarget
            ? "bg-accent ring-2 ring-primary"
            : "hover:bg-accent/60",
        )}
      >
        {hasChildren ? (
          <button
            type="button"
            onClick={() => onToggle(folder.id)}
            aria-label={isExpanded ? "Collapse" : "Expand"}
            className="flex h-5 w-5 items-center justify-center rounded hover:bg-accent"
          >
            {isExpanded ? (
              <ChevronDown className="h-3.5 w-3.5" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5" />
            )}
          </button>
        ) : (
          <span className="inline-block h-5 w-5" aria-hidden />
        )}
        <FolderIcon className="h-4 w-4 text-muted-foreground" />
        {/* Click the name to browse the folder's documents; the row itself
            stays draggable for reparenting. */}
        <Link
          href={`/folders/${folder.id}`}
          draggable={false}
          onClick={(e) => e.stopPropagation()}
          className="font-medium hover:underline"
        >
          {folder.name}
        </Link>
        <span className="truncate font-mono text-xs text-muted-foreground">{folder.dot_path}</span>
        <button
          type="button"
          aria-label="Folder actions"
          onClick={open}
          className="ml-auto rounded p-1 text-muted-foreground opacity-0 hover:bg-accent hover:text-foreground focus:opacity-100 group-hover:opacity-100"
        >
          <MoreVertical className="h-4 w-4" />
        </button>
      </div>
      {menu}
      <FolderProperties
        folder={folder}
        open={propsOpen}
        onClose={() => setPropsOpen(false)}
        onSaved={onChanged}
      />

      {isExpanded && hasChildren ? (
        <ul className="space-y-0.5">
          {children.map((child) => (
            <TreeRow
              key={child.folder.id}
              node={child}
              depth={depth + 1}
              folders={folders}
              expanded={expanded}
              dragOverId={dragOverId}
              onToggle={onToggle}
              onChanged={onChanged}
              onDragStart={onDragStart}
              onDragOver={onDragOver}
              onDragLeave={onDragLeave}
              onDrop={onDrop}
            />
          ))}
        </ul>
      ) : null}
    </li>
  );
}
