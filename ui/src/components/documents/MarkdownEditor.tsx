"use client";

import type { EditorView } from "@codemirror/view";
import {
  AlignCenter,
  AlignLeft,
  AlignRight,
  Bold,
  Code,
  Columns2,
  Eye,
  Heading1,
  Heading2,
  Heading3,
  Italic,
  Link as LinkIcon,
  List,
  ListOrdered,
  Pencil,
  Quote,
  SquareCode,
  Table as TableIcon,
} from "lucide-react";
import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { Markdown } from "@/components/common/Markdown";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { createDocument, getDocumentContent, updateDocumentContent } from "@/lib/api/documents";
import { cn } from "@/lib/utils";

import { applyMarkdown, type MarkdownCommand } from "./markdownCommands";
import {
  addColumn,
  addRow,
  deleteColumn,
  deleteRow,
  findTableAt,
  type ParsedTable,
  serializeTable,
  setAlign,
} from "./markdownTable";

// CodeMirror is client-only (touches the DOM); never render it on the server.
const CodeMirrorPane = dynamic(() => import("./CodeMirrorPane"), {
  ssr: false,
  loading: () => <div className="h-full w-full animate-pulse bg-muted/30" />,
});

type PaneMode = "edit" | "split" | "preview";

interface MarkdownEditorProps {
  open: boolean;
  onClose: () => void;
  /** Called after a successful save (create or edit) so the caller can reload. */
  onSaved: () => void;
  /** Create mode: the folder the new document is filed into. */
  defaultFolderId?: string | null;
  /** Edit mode: the document whose body is being edited (omit to create). */
  documentId?: string;
  /** Edit mode: the document's current title (shown read-only). */
  initialTitle?: string;
}

/** Ensure a created document has a `.md` name so the explorer offers "Edit". */
function withMarkdownExtension(title: string): string {
  const trimmed = title.trim();
  return /\.[a-z0-9]+$/i.test(trimmed) ? trimmed : `${trimmed}.md`;
}

interface ToolbarButton {
  command: MarkdownCommand;
  label: string;
  icon: React.ReactNode;
}

const TOOLBAR: ToolbarButton[] = [
  { command: "bold", label: "Bold", icon: <Bold className="h-4 w-4" /> },
  { command: "italic", label: "Italic", icon: <Italic className="h-4 w-4" /> },
  { command: "h1", label: "Heading 1", icon: <Heading1 className="h-4 w-4" /> },
  { command: "h2", label: "Heading 2", icon: <Heading2 className="h-4 w-4" /> },
  { command: "h3", label: "Heading 3", icon: <Heading3 className="h-4 w-4" /> },
  { command: "ul", label: "Bullet list", icon: <List className="h-4 w-4" /> },
  { command: "ol", label: "Numbered list", icon: <ListOrdered className="h-4 w-4" /> },
  { command: "quote", label: "Quote", icon: <Quote className="h-4 w-4" /> },
  { command: "link", label: "Link", icon: <LinkIcon className="h-4 w-4" /> },
  { command: "code", label: "Inline code", icon: <Code className="h-4 w-4" /> },
  { command: "codeblock", label: "Code block", icon: <SquareCode className="h-4 w-4" /> },
  { command: "table", label: "Table", icon: <TableIcon className="h-4 w-4" /> },
];

/**
 * Markdown-native editor with a live preview. Raw Markdown is the source of
 * truth (CodeMirror), so formatting round-trips losslessly. Two modes:
 *   - create (no `documentId`): POST a new document into `defaultFolderId`.
 *   - edit (`documentId`): load via getDocumentContent, PUT via updateDocumentContent.
 */
export function MarkdownEditor({
  open,
  onClose,
  onSaved,
  defaultFolderId,
  documentId,
  initialTitle,
}: MarkdownEditorProps) {
  const isEdit = Boolean(documentId);
  const [title, setTitle] = useState(initialTitle ?? "");
  const [value, setValue] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [mode, setMode] = useState<PaneMode>("split");
  const [cursor, setCursor] = useState(0);
  const viewRef = useRef<EditorView | null>(null);

  // Load existing content when editing; reset cleanly when (re)opening to create.
  useEffect(() => {
    if (!open) return;
    setDirty(false);
    setMode("split");
    if (!documentId) {
      setTitle(initialTitle ?? "");
      setValue("");
      return;
    }
    let cancelled = false;
    setLoading(true);
    getDocumentContent(documentId)
      .then((res) => {
        if (cancelled) return;
        setTitle(initialTitle ?? "");
        setValue(res.content ?? "");
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        toast.error(e instanceof Error ? e.message : "Failed to load document");
        onClose();
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, documentId, initialTitle, onClose]);

  const handleChange = useCallback((next: string) => {
    setValue(next);
    setDirty(true);
  }, []);

  const runCommand = useCallback((command: MarkdownCommand) => {
    if (viewRef.current) applyMarkdown(viewRef.current, command);
  }, []);

  const handleCursor = useCallback((head: number) => setCursor(head), []);

  // The Markdown table (if any) the caret is currently inside. Drives the
  // contextual table toolbar. Recomputed from the live doc + caret; cheap.
  const activeTable = useMemo<ParsedTable | null>(
    () => (mode === "preview" ? null : findTableAt(value, cursor)),
    [value, cursor, mode],
  );

  // Apply a table transform to the block under the caret and write it back.
  // Reads the freshest table straight from the view so it targets the caret's
  // current row/column even if React state is a tick behind.
  const applyTableOp = useCallback((op: (t: ParsedTable) => ParsedTable) => {
    const view = viewRef.current;
    if (!view) return;
    const t = findTableAt(view.state.doc.toString(), view.state.selection.main.head);
    if (!t) return;
    const next = serializeTable(op(t));
    view.dispatch({
      changes: { from: t.from, to: t.to, insert: next },
      selection: { anchor: t.from },
      scrollIntoView: true,
    });
    view.focus();
  }, []);

  const save = useCallback(async () => {
    if (saving) return;
    if (!isEdit && !title.trim()) {
      toast.error("Title is required");
      return;
    }
    setSaving(true);
    try {
      if (isEdit && documentId) {
        await updateDocumentContent(documentId, value);
      } else {
        await createDocument({
          title: withMarkdownExtension(title),
          text: value,
          folder_id: defaultFolderId ?? undefined,
        });
      }
      toast.success(isEdit ? "Document saved" : "Document created");
      setDirty(false);
      onSaved();
      onClose();
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : "Failed to save document");
    } finally {
      setSaving(false);
    }
  }, [saving, isEdit, documentId, title, value, defaultFolderId, onSaved, onClose]);

  const requestClose = useCallback(() => {
    if (dirty && !window.confirm("Discard unsaved changes?")) return;
    onClose();
  }, [dirty, onClose]);

  // Cmd/Ctrl+S saves without leaving the editor.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        void save();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, save]);

  const showEditor = mode !== "preview";
  const showPreview = mode !== "edit";

  return (
    <Dialog open={open} onClose={requestClose} className="flex h-[85vh] w-full max-w-6xl flex-col p-0">
      {/* Header */}
      <div className="flex items-center gap-3 border-b px-4 py-3 pr-12">
        <div className="min-w-0 flex-1">
          {isEdit ? (
            <div className="truncate text-sm font-semibold" title={title}>
              {title || "Untitled"}
            </div>
          ) : (
            <Input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Document title (e.g. Runbook.md)"
              className="h-8 max-w-md"
              autoFocus
            />
          )}
        </div>
        <Button type="button" variant="ghost" size="sm" onClick={requestClose} disabled={saving}>
          Cancel
        </Button>
        <Button type="button" size="sm" onClick={() => void save()} disabled={saving || loading}>
          {saving ? "Saving…" : "Save"}
        </Button>
      </div>

      {/* Formatting toolbar + view toggle */}
      <div className="flex items-center gap-1 border-b bg-muted/30 px-2 py-1">
        <div className="flex flex-wrap items-center gap-0.5">
          {TOOLBAR.map((b) => (
            <button
              key={b.command}
              type="button"
              title={b.label}
              aria-label={b.label}
              disabled={loading || !showEditor}
              onClick={() => runCommand(b.command)}
              className="flex h-7 w-7 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-40"
            >
              {b.icon}
            </button>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-0.5">
          <ModeButton active={mode === "edit"} onClick={() => setMode("edit")} title="Editor only">
            <Pencil className="h-4 w-4" />
          </ModeButton>
          <ModeButton active={mode === "split"} onClick={() => setMode("split")} title="Split view">
            <Columns2 className="h-4 w-4" />
          </ModeButton>
          <ModeButton active={mode === "preview"} onClick={() => setMode("preview")} title="Preview only">
            <Eye className="h-4 w-4" />
          </ModeButton>
        </div>
      </div>

      {/* Contextual table toolbar — appears only while the caret is inside a
          Markdown table. Buttons rewrite the table's Markdown in place. */}
      {activeTable ? (
        <div className="flex flex-wrap items-center gap-1 border-b bg-accent/40 px-2 py-1 text-xs">
          <span className="mr-1 inline-flex items-center gap-1 font-medium text-muted-foreground">
            <TableIcon className="h-3.5 w-3.5" />
            Table
          </span>
          <TableAction label="Add column" onClick={() => applyTableOp((t) => addColumn(t, t.cursorCol))} />
          <TableAction
            label="Delete column"
            disabled={activeTable.header.length <= 1}
            onClick={() => applyTableOp((t) => deleteColumn(t, t.cursorCol))}
          />
          <span className="mx-0.5 h-4 w-px bg-border" />
          <TableAction label="Add row" onClick={() => applyTableOp((t) => addRow(t, t.cursorRow))} />
          <TableAction
            label="Delete row"
            disabled={activeTable.cursorRow < 0}
            onClick={() => applyTableOp((t) => deleteRow(t, t.cursorRow))}
          />
          <span className="mx-0.5 h-4 w-px bg-border" />
          <span className="text-muted-foreground">Align</span>
          <TableIconAction
            label="Align left"
            onClick={() => applyTableOp((t) => setAlign(t, t.cursorCol, "left"))}
          >
            <AlignLeft className="h-3.5 w-3.5" />
          </TableIconAction>
          <TableIconAction
            label="Align center"
            onClick={() => applyTableOp((t) => setAlign(t, t.cursorCol, "center"))}
          >
            <AlignCenter className="h-3.5 w-3.5" />
          </TableIconAction>
          <TableIconAction
            label="Align right"
            onClick={() => applyTableOp((t) => setAlign(t, t.cursorCol, "right"))}
          >
            <AlignRight className="h-3.5 w-3.5" />
          </TableIconAction>
        </div>
      ) : null}

      {/* Body: source + preview */}
      <div className="flex min-h-0 flex-1">
        {showEditor ? (
          <div className={cn("min-w-0 overflow-hidden", showPreview ? "w-1/2 border-r" : "w-full")}>
            {loading ? (
              <div className="h-full w-full animate-pulse bg-muted/30" />
            ) : (
              <CodeMirrorPane
                value={value}
                onChange={handleChange}
                onCursor={handleCursor}
                onReady={(view) => {
                  viewRef.current = view;
                }}
              />
            )}
          </div>
        ) : null}
        {showPreview ? (
          <div className={cn("min-w-0 overflow-y-auto p-4", showEditor ? "w-1/2" : "w-full")}>
            {value.trim() ? (
              <Markdown content={value} />
            ) : (
              <p className="text-sm text-muted-foreground">Nothing to preview yet.</p>
            )}
          </div>
        ) : null}
      </div>
    </Dialog>
  );
}

function TableAction({
  label,
  onClick,
  disabled,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="rounded px-1.5 py-0.5 text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-40"
    >
      {label}
    </button>
  );
}

function TableIconAction({
  label,
  onClick,
  children,
}: {
  label: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      onClick={onClick}
      className="flex h-6 w-6 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-foreground"
    >
      {children}
    </button>
  );
}

function ModeButton({
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
