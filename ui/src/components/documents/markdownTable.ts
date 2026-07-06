/**
 * Pure helpers to locate and edit a GFM Markdown table around a cursor. The
 * editor keeps raw Markdown as the source of truth, so "add column / row" just
 * parses the table block, transforms a plain data model, and re-serializes it —
 * no WYSIWYG round-trip, no lost fidelity.
 */

export type Align = "none" | "left" | "center" | "right";

export interface ParsedTable {
  /** Doc offsets of the table block (header line start … last row line end). */
  from: number;
  to: number;
  header: string[];
  aligns: Align[];
  rows: string[][];
  /** Cursor location within the table: -1 = header/delimiter, else body row. */
  cursorRow: number;
  cursorCol: number;
}

// A delimiter line: pipe-separated runs of dashes with optional alignment colons.
const DELIM_RE = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;

interface DocLine {
  text: string;
  from: number;
  to: number;
}

function getLines(doc: string): DocLine[] {
  const lines: DocLine[] = [];
  let from = 0;
  for (const text of doc.split("\n")) {
    const to = from + text.length;
    lines.push({ text, from, to });
    from = to + 1; // skip the "\n"
  }
  return lines;
}

/** Split a table row into trimmed cells, honouring escaped `\|` pipes. */
function splitCells(line: string): string[] {
  const s = line.trim().replace(/^\|/, "").replace(/\|$/, "");
  return s.split(/(?<!\\)\|/).map((c) => c.trim());
}

function parseAlign(cell: string): Align {
  const c = cell.trim();
  const left = c.startsWith(":");
  const right = c.endsWith(":");
  if (left && right) return "center";
  if (right) return "right";
  if (left) return "left";
  return "none";
}

function insertAt<T>(arr: T[], index: number, value: T): T[] {
  const next = [...arr];
  next.splice(index, 0, value);
  return next;
}

function removeAt<T>(arr: T[], index: number): T[] {
  const next = [...arr];
  next.splice(index, 1);
  return next;
}

function padRow(row: string[], cols: number): string[] {
  const next = row.slice(0, cols);
  while (next.length < cols) next.push("");
  return next;
}

/**
 * Find the Markdown table block containing `cursor`, or null if the cursor is
 * not inside one. A block is a header line, a delimiter line, and the contiguous
 * following lines that contain a pipe (GFM ends a table at the first non-row).
 */
export function findTableAt(doc: string, cursor: number): ParsedTable | null {
  const lines = getLines(doc);
  const cursorLine = lines.findIndex((l) => cursor >= l.from && cursor <= l.to);
  if (cursorLine < 0) return null;

  for (let i = 1; i < lines.length; i++) {
    if (!DELIM_RE.test(lines[i]!.text)) continue;
    const headerLine = i - 1;
    if (lines[headerLine]!.text.trim() === "" || DELIM_RE.test(lines[headerLine]!.text)) continue;

    // Body runs while lines still look like table rows (contain a pipe).
    let last = i;
    for (let j = i + 1; j < lines.length; j++) {
      if (lines[j]!.text.trim() === "" || !lines[j]!.text.includes("|")) break;
      last = j;
    }
    if (cursorLine < headerLine || cursorLine > last) continue;

    const header = splitCells(lines[headerLine]!.text);
    const cols = header.length;
    const aligns = padAligns(splitCells(lines[i]!.text).map(parseAlign), cols);
    const rows: string[][] = [];
    for (let j = i + 1; j <= last; j++) rows.push(padRow(splitCells(lines[j]!.text), cols));

    const { cursorRow, cursorCol } = locateCursor(lines, cursorLine, headerLine, i, cursor, cols);
    return {
      from: lines[headerLine]!.from,
      to: lines[last]!.to,
      header,
      aligns,
      rows,
      cursorRow,
      cursorCol,
    };
  }
  return null;
}

function padAligns(aligns: Align[], cols: number): Align[] {
  const next = aligns.slice(0, cols);
  while (next.length < cols) next.push("none");
  return next;
}

function locateCursor(
  lines: DocLine[],
  cursorLine: number,
  headerLine: number,
  delimLine: number,
  cursor: number,
  cols: number,
): { cursorRow: number; cursorCol: number } {
  const line = lines[cursorLine]!;
  const before = line.text.slice(0, cursor - line.from);
  const pipes = (before.match(/(?<!\\)\|/g) ?? []).length;
  const hasLeadingPipe = /^\s*\|/.test(line.text);
  const cursorCol = Math.min(Math.max(pipes - (hasLeadingPipe ? 1 : 0), 0), cols - 1);
  const cursorRow = cursorLine <= delimLine ? -1 : cursorLine - (delimLine + 1);
  return { cursorRow, cursorCol };
}

/** Insert a blank column immediately after `at` (clamped in-range). */
export function addColumn(t: ParsedTable, at: number): ParsedTable {
  const idx = Math.min(Math.max(at + 1, 0), t.header.length);
  return {
    ...t,
    header: insertAt(t.header, idx, ""),
    aligns: insertAt(t.aligns, idx, "none"),
    rows: t.rows.map((r) => insertAt(r, idx, "")),
  };
}

/** Remove column `at`; a table must keep at least one column. */
export function deleteColumn(t: ParsedTable, at: number): ParsedTable {
  if (t.header.length <= 1 || at < 0 || at >= t.header.length) return t;
  return {
    ...t,
    header: removeAt(t.header, at),
    aligns: removeAt(t.aligns, at),
    rows: t.rows.map((r) => removeAt(r, at)),
  };
}

/** Insert a blank body row after body index `afterRow` (-1/header ⇒ top). */
export function addRow(t: ParsedTable, afterRow: number): ParsedTable {
  const empty = t.header.map(() => "");
  const idx = afterRow < 0 ? 0 : Math.min(afterRow + 1, t.rows.length);
  return { ...t, rows: insertAt(t.rows, idx, empty) };
}

/** Remove body row `at` (header/delimiter cannot be removed). */
export function deleteRow(t: ParsedTable, at: number): ParsedTable {
  if (at < 0 || at >= t.rows.length) return t;
  return { ...t, rows: removeAt(t.rows, at) };
}

/** Set column `at`'s alignment. */
export function setAlign(t: ParsedTable, at: number, align: Align): ParsedTable {
  if (at < 0 || at >= t.aligns.length) return t;
  const aligns = [...t.aligns];
  aligns[at] = align;
  return { ...t, aligns };
}

function alignDashes(a: Align): string {
  switch (a) {
    case "left":
      return ":---";
    case "right":
      return "---:";
    case "center":
      return ":---:";
    default:
      return "---";
  }
}

/** Escape unescaped pipes so cell content can't break the table syntax. */
function escapeCell(cell: string): string {
  return cell.replace(/(?<!\\)\|/g, "\\|");
}

/** Render a table model back to GFM Markdown (normalized, aligned pipes). */
export function serializeTable(t: ParsedTable): string {
  const cols = t.header.length;
  const headerLine = `| ${t.header.map(escapeCell).join(" | ")} |`;
  const delimLine = `| ${t.aligns.map(alignDashes).join(" | ")} |`;
  const bodyLines = t.rows.map(
    (r) => `| ${padRow(r, cols).map(escapeCell).join(" | ")} |`,
  );
  return [headerLine, delimLine, ...bodyLines].join("\n");
}
