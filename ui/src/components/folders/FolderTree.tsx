"use client";

import { ChevronDown, ChevronRight, Folder as FolderIcon } from "lucide-react";
import { useMemo, useState } from "react";

import { cn } from "@/lib/utils";
import type { Folder } from "@/types";

interface FolderTreeProps {
  folders: Folder[];
  onMove: (folderId: string, newParentId: string | null) => void | Promise<void>;
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

export function FolderTree({ folders, onMove }: FolderTreeProps) {
  const tree = useMemo(() => buildTree(folders), [folders]);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [dragOverId, setDragOverId] = useState<string | "__root__" | null>(null);

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
            expanded={expanded}
            dragOverId={dragOverId}
            onToggle={toggle}
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
  expanded: Set<string>;
  dragOverId: string | "__root__" | null;
  onToggle: (id: string) => void;
  onDragStart: (e: React.DragEvent, folderId: string) => void;
  onDragOver: (e: React.DragEvent, targetId: string | "__root__") => void;
  onDragLeave: () => void;
  onDrop: (e: React.DragEvent, targetId: string | null) => void;
}

function TreeRow({
  node,
  depth,
  expanded,
  dragOverId,
  onToggle,
  onDragStart,
  onDragOver,
  onDragLeave,
  onDrop,
}: TreeRowProps) {
  const { folder, children } = node;
  const isExpanded = expanded.has(folder.id);
  const hasChildren = children.length > 0;
  const isDragTarget = dragOverId === folder.id;

  return (
    <li>
      <div
        draggable
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
          "flex cursor-grab items-center gap-2 rounded-md py-1.5 pr-2 text-sm transition-colors",
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
        <span className="font-medium">{folder.name}</span>
        <span className="truncate font-mono text-xs text-muted-foreground">{folder.dot_path}</span>
      </div>

      {isExpanded && hasChildren ? (
        <ul className="space-y-0.5">
          {children.map((child) => (
            <TreeRow
              key={child.folder.id}
              node={child}
              depth={depth + 1}
              expanded={expanded}
              dragOverId={dragOverId}
              onToggle={onToggle}
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
