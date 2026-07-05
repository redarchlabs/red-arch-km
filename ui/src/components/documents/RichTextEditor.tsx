"use client";

import { EditorContent, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import {
  Bold,
  Heading2,
  Italic,
  List,
  ListOrdered,
  Quote,
} from "lucide-react";
import { useEffect } from "react";

interface RichTextEditorProps {
  /** Called with the editor's current HTML on every change. */
  onChange: (html: string) => void;
  disabled?: boolean;
}

/**
 * TipTap rich-text editor. Emits HTML via onChange; the caller converts it to
 * Markdown (see lib/markdown) before submitting so the ingest pipeline never
 * sees raw HTML. immediatelyRender:false avoids the Next.js SSR hydration
 * mismatch for contenteditable.
 */
export function RichTextEditor({ onChange, disabled }: RichTextEditorProps) {
  const editor = useEditor({
    extensions: [StarterKit],
    immediatelyRender: false,
    editable: !disabled,
    editorProps: {
      attributes: {
        class:
          "rte-content min-h-[200px] w-full rounded-b-md border border-t-0 border-input bg-transparent px-3 py-2 text-sm focus:outline-none",
      },
    },
    onUpdate: ({ editor }) => onChange(editor.getHTML()),
  });

  // Keep editability in sync with the submitting/disabled state.
  useEffect(() => {
    editor?.setEditable(!disabled);
  }, [editor, disabled]);

  if (!editor) {
    return <div className="min-h-[236px] rounded-md border border-input" aria-hidden />;
  }

  const btn = (active: boolean) =>
    `flex h-7 w-7 items-center justify-center rounded hover:bg-accent ${active ? "bg-accent text-foreground" : "text-muted-foreground"}`;

  return (
    <div>
      <div className="flex gap-0.5 rounded-t-md border border-input bg-muted/30 p-1">
        <button
          type="button"
          disabled={disabled}
          aria-label="Bold"
          className={btn(editor.isActive("bold"))}
          onClick={() => editor.chain().focus().toggleBold().run()}
        >
          <Bold className="h-4 w-4" />
        </button>
        <button
          type="button"
          disabled={disabled}
          aria-label="Italic"
          className={btn(editor.isActive("italic"))}
          onClick={() => editor.chain().focus().toggleItalic().run()}
        >
          <Italic className="h-4 w-4" />
        </button>
        <button
          type="button"
          disabled={disabled}
          aria-label="Heading"
          className={btn(editor.isActive("heading", { level: 2 }))}
          onClick={() => editor.chain().focus().toggleHeading({ level: 2 }).run()}
        >
          <Heading2 className="h-4 w-4" />
        </button>
        <button
          type="button"
          disabled={disabled}
          aria-label="Bullet list"
          className={btn(editor.isActive("bulletList"))}
          onClick={() => editor.chain().focus().toggleBulletList().run()}
        >
          <List className="h-4 w-4" />
        </button>
        <button
          type="button"
          disabled={disabled}
          aria-label="Numbered list"
          className={btn(editor.isActive("orderedList"))}
          onClick={() => editor.chain().focus().toggleOrderedList().run()}
        >
          <ListOrdered className="h-4 w-4" />
        </button>
        <button
          type="button"
          disabled={disabled}
          aria-label="Quote"
          className={btn(editor.isActive("blockquote"))}
          onClick={() => editor.chain().focus().toggleBlockquote().run()}
        >
          <Quote className="h-4 w-4" />
        </button>
      </div>
      <EditorContent editor={editor} />
    </div>
  );
}
