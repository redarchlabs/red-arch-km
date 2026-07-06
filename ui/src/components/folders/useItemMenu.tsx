"use client";

import {
  FilePlus2,
  FolderPlus,
  Pencil,
  Settings2,
  SquareArrowOutUpRight,
  TextCursorInput,
  Trash2,
  Upload,
} from "lucide-react";

import { type MenuItem, useRowMenu } from "@/components/common/ActionMenu";

import { deleteEntry, renameEntry } from "./entryActions";
import { type DialogState, type ExplorerItem, isMarkdownEditable } from "./explorerItem";

/**
 * Build the shared context menu for an explorer item (folder or document).
 * Delete and Rename route through {@link deleteEntry}/{@link renameEntry}, which
 * try/catch and surface failures — the menu invokes `onSelect` fire-and-forget,
 * so an unguarded rejection here would otherwise be swallowed.
 */
export function useItemMenu(
  item: ExplorerItem,
  onOpen: (i: ExplorerItem) => void,
  onAction: (d: DialogState) => void,
  onReload: () => void,
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
      onSelect: () => void renameEntry(item.kind, item.id, item.name, onReload),
    },
    {
      label: "Delete",
      icon: <Trash2 className="h-4 w-4" />,
      destructive: true,
      onSelect: () => void deleteEntry(item.kind, item.id, item.name, onReload),
    },
  ];
  return useRowMenu(items);
}
