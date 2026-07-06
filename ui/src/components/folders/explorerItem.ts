import type { Document, Folder } from "@/types";

export type ViewMode = "details" | "list" | "large" | "small";
export type SortKey = "name" | "modified" | "size" | "type";
export type SortDir = "asc" | "desc";

// Fixed row/cell metrics for the virtualizer (react-window needs known sizes).
export const DETAILS_ROW_H = 32;
export const LIST_ROW_H = 34;
export const SMALL_CELL_H = 92;
export const LARGE_CELL_H = 116;
// Minimum cell width per grid view, used to derive the responsive column count.
export const MIN_CELL_W: Record<"list" | "small" | "large", number> = {
  list: 220,
  small: 104,
  large: 132,
};
// Shared column template so the details header and rows stay aligned.
export const DETAILS_COLS = "minmax(120px,1fr) 150px 90px 170px 90px";

export interface ExplorerItem {
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

export type DialogState =
  | { type: "subfolder"; folder: Folder }
  | { type: "upload"; folder: Folder }
  | { type: "newMarkdown"; folder: Folder }
  | { type: "editDoc"; doc: Document }
  | { type: "folderProps"; folder: Folder }
  | { type: "docProps"; doc: Document }
  | null;

/** Props shared by every view's rows/cells and the item context menu. */
export interface RowProps {
  items: ExplorerItem[];
  onOpen: (i: ExplorerItem) => void;
  onAction: (d: DialogState) => void;
  /** Reload the browser after a rename / delete (was misnamed `onDelete`). */
  onReload: () => void;
}

/** True while a document is still being extracted/indexed. */
export function isProcessing(status: Document["processing_status"]): boolean {
  return status === "PENDING" || status === "PROCESSING";
}

export function extOf(title: string): string {
  const m = /\.([a-z0-9]+)$/i.exec(title.trim());
  return m ? m[1]!.toUpperCase() : "Document";
}

// Text originals we can round-trip through the Markdown editor. Detection is by
// extension (the list has no body); authored docs get a `.md` name on create,
// so they surface "Edit" too. Non-text originals (pdf/docx) are read-only here.
const MARKDOWN_EDITABLE_EXTS = new Set(["md", "markdown", "txt"]);
export function isMarkdownEditable(title: string): boolean {
  const m = /\.([a-z0-9]+)$/i.exec(title.trim());
  return m ? MARKDOWN_EDITABLE_EXTS.has(m[1]!.toLowerCase()) : false;
}
