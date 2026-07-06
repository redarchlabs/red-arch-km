import { EditorSelection } from "@codemirror/state";
import type { EditorView } from "@codemirror/view";

/** Formatting actions the Markdown editor toolbar can apply. */
export type MarkdownCommand =
  | "bold"
  | "italic"
  | "code"
  | "link"
  | "h1"
  | "h2"
  | "h3"
  | "quote"
  | "ul"
  | "ol"
  | "codeblock"
  | "table";

/** Wrap each selection in `before`/`after`; insert `placeholder` when empty. */
function wrapSelection(view: EditorView, before: string, after: string, placeholder: string): void {
  const changes = view.state.changeByRange((range) => {
    const selected = view.state.sliceDoc(range.from, range.to) || placeholder;
    const insert = `${before}${selected}${after}`;
    const start = range.from + before.length;
    return {
      changes: { from: range.from, to: range.to, insert },
      range: EditorSelection.range(start, start + selected.length),
    };
  });
  view.dispatch(view.state.update(changes, { scrollIntoView: true }));
  view.focus();
}

/** Prefix every line touched by the main selection (headings, quotes, lists). */
function prefixLines(view: EditorView, prefix: string | ((index: number) => string)): void {
  const { state } = view;
  const range = state.selection.main;
  const startLine = state.doc.lineAt(range.from).number;
  const endLine = state.doc.lineAt(range.to).number;
  const changes = [];
  let index = 0;
  for (let ln = startLine; ln <= endLine; ln++) {
    const line = state.doc.line(ln);
    const p = typeof prefix === "function" ? prefix(index) : prefix;
    changes.push({ from: line.from, insert: p });
    index++;
  }
  view.dispatch({ changes, scrollIntoView: true });
  view.focus();
}

/** Insert `text` at the cursor, optionally leaving the caret at `cursorOffset`. */
function insertAtCursor(view: EditorView, text: string, cursorOffset?: number): void {
  const pos = view.state.selection.main.head;
  view.dispatch({
    changes: { from: pos, insert: text },
    selection: { anchor: pos + (cursorOffset ?? text.length) },
    scrollIntoView: true,
  });
  view.focus();
}

/** Insert a Markdown link around the selection, leaving "url" pre-selected. */
function insertLink(view: EditorView): void {
  const { state } = view;
  const range = state.selection.main;
  const selected = state.sliceDoc(range.from, range.to) || "text";
  const insert = `[${selected}](url)`;
  const urlStart = range.from + selected.length + 3; // past "[selected]("
  view.dispatch({
    changes: { from: range.from, to: range.to, insert },
    selection: EditorSelection.range(urlStart, urlStart + 3), // select "url"
    scrollIntoView: true,
  });
  view.focus();
}

const TABLE_SNIPPET = "\n| Column A | Column B |\n| --- | --- |\n| Cell 1 | Cell 2 |\n";

/** Apply a toolbar formatting action to the CodeMirror view. */
export function applyMarkdown(view: EditorView, command: MarkdownCommand): void {
  switch (command) {
    case "bold":
      return wrapSelection(view, "**", "**", "bold text");
    case "italic":
      return wrapSelection(view, "*", "*", "italic text");
    case "code":
      return wrapSelection(view, "`", "`", "code");
    case "link":
      return insertLink(view);
    case "h1":
      return prefixLines(view, "# ");
    case "h2":
      return prefixLines(view, "## ");
    case "h3":
      return prefixLines(view, "### ");
    case "quote":
      return prefixLines(view, "> ");
    case "ul":
      return prefixLines(view, "- ");
    case "ol":
      return prefixLines(view, (i) => `${i + 1}. `);
    case "codeblock":
      return wrapSelection(view, "```\n", "\n```", "code");
    case "table":
      return insertAtCursor(view, TABLE_SNIPPET);
  }
}
