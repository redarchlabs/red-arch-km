/**
 * Turn a document's indexed chunks into readable blocks for the reader.
 *
 * The reader falls back to raw chunk text whenever there is no readable
 * original — PDFs, images, anything OCR'd. Those chunks are cut for retrieval,
 * not for reading: the ingest chunker splits on the sentence splitter's output,
 * and for extracted PDF text every *line* looks like a sentence, so a chunk
 * routinely ends mid-sentence ("…and Eliud begot") with the rest of it opening
 * the next chunk ("Eleazar; and…"). Consecutive chunks also share an overlap
 * window, so the seam repeats a sentence or two verbatim.
 *
 * Both defects live at the seam, so both are fixed there: drop the repeated
 * head, then move the tail of a split sentence back onto the block that started
 * it. Blocks stay one-per-chunk — each keeps its own summary and citation
 * anchor — only the exact cut point moves.
 */

/** Longest repeat we will treat as the chunker's overlap window. */
const MAX_OVERLAP_CHARS = 600;
/**
 * Shortest repeat we will drop. Below this a match is more likely to be a
 * phrase the document happens to repeat ("and he said to them") than the
 * chunker's overlap, and deleting it would lose real text.
 */
const MIN_OVERLAP_CHARS = 40;
/**
 * How far into the next chunk we will look for a place to end the previous
 * block. Beyond this the "fix" would move a screenful of text under the wrong
 * summary, which reads worse than the split sentence does.
 */
const COMPLETION_WINDOW_CHARS = 400;

/** Sentence terminator, plus any closing quote/bracket, at a word boundary. */
const SENTENCE_END_RE = /[.!?…]["'”’)\]]*(?=\s+[^a-z]|\s*$)/g;
/** Same, anchored at the end — "does this text stop at a sentence?" */
const ENDS_SENTENCE_RE = /[.!?…]["'”’)\]]*$/;
/** Clause terminator — the fallback ending when no sentence ends in range. */
const CLAUSE_END_RE = /[;:]["'”’)\]]*(?=\s)/g;

export interface ReaderBlockChunk {
  chunk_order: number;
  text: string;
  summary?: string | null;
}

export interface ReaderBlock {
  /** The chunk this block came from — drives its citation anchor id. */
  chunkOrder: number;
  summary: string | null;
  /** Chunk text with the seam cleaned up; may be empty for a duplicate chunk. */
  text: string;
}

/** Chunk text arrives whitespace-flattened; make that guarantee explicit. */
function flatten(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}

/**
 * `next` with the chunker's overlap removed — the longest head of `next` that
 * `prev` already ends with. Returns `next` unchanged when the repeat is too
 * short to be an overlap window.
 */
function stripOverlap(prev: string, next: string): string {
  const longest = Math.min(prev.length, next.length, MAX_OVERLAP_CHARS);
  for (let size = longest; size >= MIN_OVERLAP_CHARS; size -= 1) {
    if (prev.endsWith(next.slice(0, size))) return next.slice(size).trimStart();
  }
  // The overlap window can open mid-line, leaving a repeat too short to trust on
  // length alone ("…cast into the fire." / "the fire. I indeed immerse…"). It is
  // still a repeat when it ends on the very sentence the previous block ends on.
  const end = firstEndIndex(SENTENCE_END_RE, next.slice(0, MIN_OVERLAP_CHARS));
  if (end > 0 && prev.endsWith(next.slice(0, end))) return next.slice(end).trimStart();
  return next;
}

/** Index just past the first match of `re` within `text`, or -1. */
function firstEndIndex(re: RegExp, text: string): number {
  re.lastIndex = 0;
  const match = re.exec(text);
  return match === null ? -1 : match.index + match[0].length;
}

/**
 * The head of `next` that finishes the sentence `prev` was cut off in, or ""
 * when `prev` already ends cleanly, nothing readable ends within
 * {@link COMPLETION_WINDOW_CHARS}, or moving it would leave `next` empty.
 */
function sentenceCompletion(prev: string, next: string): string {
  if (!prev || !next) return "";
  if (ENDS_SENTENCE_RE.test(prev)) return "";
  const window = next.slice(0, COMPLETION_WINDOW_CHARS);
  const sentence = firstEndIndex(SENTENCE_END_RE, window);
  const end = sentence !== -1 ? sentence : firstEndIndex(CLAUSE_END_RE, window);
  if (end === -1) return "";
  const completion = next.slice(0, end);
  // The next block keeps its own text: a seam is not worth an empty section.
  return next.slice(end).trim() ? completion : "";
}

/**
 * Cut the loaded chunks into reading blocks, one per chunk, with each seam
 * moved to the nearest sentence (or clause) ending and the chunker's overlap
 * dropped.
 *
 * Chunks must be in document order — the reader loads them that way. The last
 * block's seam is only fixed once the following chunk has been paged in, since
 * the completion has to come from somewhere.
 */
export function buildReaderBlocks(chunks: readonly ReaderBlockChunk[]): ReaderBlock[] {
  const blocks: ReaderBlock[] = [];
  // The block being assembled: it is only final once we have seen the next
  // chunk and know whether it owes this one the end of a sentence.
  let pending: ReaderBlock | null = null;

  for (const chunk of chunks) {
    const summary = chunk.summary?.trim() ? chunk.summary : null;
    let text = flatten(chunk.text);
    if (pending !== null) {
      text = stripOverlap(pending.text, text);
      const completion = sentenceCompletion(pending.text, text);
      blocks.push({
        ...pending,
        text: completion ? `${pending.text} ${completion}`.trim() : pending.text,
      });
      text = text.slice(completion.length).trimStart();
    }
    pending = { chunkOrder: chunk.chunk_order, summary, text };
  }

  if (pending !== null) blocks.push(pending);
  return blocks;
}
