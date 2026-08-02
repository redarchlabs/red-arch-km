/**
 * Align a document's indexed chunks back onto its ORIGINAL source text.
 *
 * The reader's embedded view shows each section summary above the text it
 * summarizes. The chunks it gets back from the index are whitespace-flattened
 * (the chunker rejoins sentences with a single space), so rendering chunk text
 * directly shows Markdown source — `## Heading`, `**bold**` — as literal
 * characters. Instead we keep the original file and only use the chunks to
 * decide WHERE to cut it, so each slice still renders as real Markdown.
 *
 * Matching is done on a whitespace-normalized copy of the original with an
 * index map back to raw offsets, because that is the only difference the
 * chunker introduces.
 */

/** How many normalized characters of a chunk to match on. */
const PROBE_CHARS = 60;
/** Shorter retry probe for chunks whose head was altered (e.g. trailing edits). */
const MIN_PROBE_CHARS = 20;
/** Opening/closing fence of a Markdown code block (up to 3 leading spaces). */
const FENCE_RE = /^ {0,3}(?:```|~~~)/gm;
/** An ATX heading line — always opens a new block, blank line above or not. */
const HEADING_RE = /^ {0,3}#{1,6}[ \t]/;

export interface ReaderChunkLike {
  chunk_order: number;
  text: string;
  summary?: string | null;
}

export interface SegmentSummary {
  chunkOrder: number;
  summary: string | null;
}

export interface ReaderSegment {
  /** Summaries introducing this slice, in document order (may be empty). */
  summaries: SegmentSummary[];
  /** The raw original text for this slice — render it as Markdown. */
  text: string;
}

interface Normalized {
  /** Whitespace runs collapsed to a single space. */
  norm: string;
  /** `map[i]` is the offset in the source of `norm[i]`. */
  map: number[];
}

function normalize(source: string): Normalized {
  const chars: string[] = [];
  const map: number[] = [];
  let inWhitespace = false;
  for (let i = 0; i < source.length; i += 1) {
    const ch = source[i];
    if (ch === " " || ch === "\t" || ch === "\n" || ch === "\r" || ch === "\f" || ch === "\v") {
      if (!inWhitespace) {
        chars.push(" ");
        map.push(i);
        inWhitespace = true;
      }
      continue;
    }
    inWhitespace = false;
    chars.push(ch);
    map.push(i);
  }
  return { norm: chars.join(""), map };
}

function normalizeChunk(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}

/** Normalized index where `chunk` starts in `norm`, searching from `from`. */
function findChunkStart(norm: string, chunkText: string, from: number): number | null {
  const flat = normalizeChunk(chunkText);
  if (!flat) return null;
  for (const size of [PROBE_CHARS, MIN_PROBE_CHARS]) {
    const probe = flat.slice(0, size);
    if (probe.length < Math.min(size, flat.length)) continue;
    const at = norm.indexOf(probe, from);
    if (at !== -1) return at;
  }
  return null;
}

/**
 * Move `offset` back to the start of its line when only whitespace precedes it
 * there. Returns null when the offset sits mid-line — cutting there would strip
 * a list bullet or split inline emphasis, so the caller merges instead.
 */
function snapToLineStart(source: string, offset: number): number | null {
  let i = offset;
  while (i > 0 && source[i - 1] !== "\n") {
    const prev = source[i - 1];
    if (prev !== " " && prev !== "\t" && prev !== "\r") return null;
    i -= 1;
  }
  return i;
}

/**
 * True when a line start opens a new block — the only place a cut is safe.
 * Cutting between the soft-wrapped lines of one paragraph, or between a list's
 * items, would render the halves as separate blocks and lose the bullet, so a
 * boundary must follow a blank line (or be a heading, which opens a block on
 * its own).
 */
function isBlockBoundary(source: string, lineStart: number): boolean {
  if (lineStart === 0) return true;
  const lineEnd = source.indexOf("\n", lineStart);
  const line = source.slice(lineStart, lineEnd === -1 ? source.length : lineEnd);
  if (HEADING_RE.test(line)) return true;
  const prevStart = source.lastIndexOf("\n", lineStart - 2) + 1;
  return source.slice(prevStart, lineStart - 1).trim() === "";
}

/** True when `offset` falls inside a fenced code block (odd number of fences). */
function isInsideFence(source: string, offset: number): boolean {
  const head = source.slice(0, offset);
  FENCE_RE.lastIndex = 0;
  const fences = head.match(FENCE_RE);
  return fences != null && fences.length % 2 === 1;
}

/**
 * Cut `original` into slices introduced by the chunks' summaries.
 *
 * A chunk whose start cannot be located, lands inside a block, falls inside a
 * code fence, or would move backwards contributes its summary to the slice
 * already being built rather than forcing a cut — the text stays renderable and
 * no summary is lost.
 */
export function segmentOriginalByChunks(
  original: string,
  chunks: readonly ReaderChunkLike[],
): ReaderSegment[] {
  if (!original.trim()) return [];
  const { norm, map } = normalize(original);
  const boundaries: { offset: number; summaries: SegmentSummary[] }[] = [
    { offset: 0, summaries: [] },
  ];
  let cursor = 0;
  let lastOffset = 0;

  for (const chunk of chunks) {
    const summary: SegmentSummary = {
      chunkOrder: chunk.chunk_order,
      summary: chunk.summary ?? null,
    };
    const found = findChunkStart(norm, chunk.text, cursor);
    const current = boundaries[boundaries.length - 1];
    if (found === null) {
      current.summaries.push(summary);
      continue;
    }
    // Overlapping chunks can restart inside the previous one, so advance by one
    // character only — reading order still forces matches to move forward.
    cursor = found + 1;
    const raw = map[found];
    const snapped = snapToLineStart(original, raw);
    if (
      snapped === null ||
      snapped < lastOffset ||
      !isBlockBoundary(original, snapped) ||
      isInsideFence(original, snapped)
    ) {
      current.summaries.push(summary);
      continue;
    }
    if (snapped === current.offset) {
      current.summaries.push(summary);
      continue;
    }
    lastOffset = snapped;
    boundaries.push({ offset: snapped, summaries: [summary] });
  }

  const segments: ReaderSegment[] = [];
  for (let i = 0; i < boundaries.length; i += 1) {
    const start = boundaries[i].offset;
    const end = i + 1 < boundaries.length ? boundaries[i + 1].offset : original.length;
    const text = original.slice(start, end);
    // A leading slice before the first chunk is dropped when it is only
    // whitespace; anything with content or a summary is kept.
    if (!text.trim() && boundaries[i].summaries.length === 0) continue;
    segments.push({ summaries: boundaries[i].summaries, text });
  }
  return segments;
}
