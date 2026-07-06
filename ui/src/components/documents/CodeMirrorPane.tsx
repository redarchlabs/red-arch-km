"use client";

import { markdown, markdownLanguage } from "@codemirror/lang-markdown";
import { languages } from "@codemirror/language-data";
import { EditorView } from "@codemirror/view";
import CodeMirror from "@uiw/react-codemirror";

interface CodeMirrorPaneProps {
  value: string;
  onChange: (value: string) => void;
  /** Hand the underlying EditorView up so the toolbar can dispatch edits. */
  onReady: (view: EditorView) => void;
  /** Fires with the caret offset whenever the selection or document changes. */
  onCursor?: (head: number) => void;
  editable?: boolean;
}

// Fill the flex parent; monospace for a source-editing feel; wrap long lines so
// Markdown source never scrolls horizontally.
const paneTheme = EditorView.theme({
  "&": { height: "100%", fontSize: "13px" },
  ".cm-scroller": { fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" },
  ".cm-content": { paddingBottom: "40vh" },
});

/**
 * Markdown source editor (CodeMirror 6). Loaded client-side only via
 * next/dynamic — CodeMirror touches the DOM and must not render on the server.
 */
export default function CodeMirrorPane({
  value,
  onChange,
  onReady,
  onCursor,
  editable = true,
}: CodeMirrorPaneProps) {
  const cursorListener = EditorView.updateListener.of((u) => {
    if (onCursor && (u.selectionSet || u.docChanged)) {
      onCursor(u.state.selection.main.head);
    }
  });
  return (
    <CodeMirror
      value={value}
      height="100%"
      editable={editable}
      onChange={onChange}
      onCreateEditor={(view) => onReady(view)}
      extensions={[
        markdown({ base: markdownLanguage, codeLanguages: languages }),
        EditorView.lineWrapping,
        paneTheme,
        cursorListener,
      ]}
      basicSetup={{ lineNumbers: false, foldGutter: false, highlightActiveLine: false }}
      className="h-full"
    />
  );
}
