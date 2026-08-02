import { describe, expect, it } from "vitest";

import { type ReaderChunkLike, segmentOriginalByChunks } from "./readerSegments";

/** Mirrors the ingest chunker: sentences rejoined with single spaces. */
function flatten(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}

function chunk(order: number, text: string, summary: string | null = `s${order}`): ReaderChunkLike {
  return { chunk_order: order, text: flatten(text), summary };
}

const DOC = `# Jude (King James Version)

**Jude** — New Testament, book 65 of 66.

## Jude 1

1 Jude, the servant of Jesus Christ, and brother of James, to them that are
sanctified by God the Father.

## Closing

24 Now unto him that is able to keep you from falling.
`;

describe("segmentOriginalByChunks", () => {
  it("cuts the original at heading boundaries and keeps Markdown source intact", () => {
    const segments = segmentOriginalByChunks(DOC, [
      chunk(0, "# Jude (King James Version) **Jude** — New Testament, book 65 of 66."),
      chunk(1, "## Jude 1 1 Jude, the servant of Jesus Christ, and brother of James"),
      chunk(2, "## Closing 24 Now unto him that is able to keep you from falling."),
    ]);

    expect(segments).toHaveLength(3);
    expect(segments[0].summaries.map((s) => s.chunkOrder)).toEqual([0]);
    expect(segments[0].text).toContain("# Jude (King James Version)");
    expect(segments[1].text.startsWith("## Jude 1")).toBe(true);
    expect(segments[2].text.startsWith("## Closing")).toBe(true);
    // Every character of the original survives the split.
    expect(segments.map((s) => s.text).join("")).toBe(DOC);
  });

  it("keeps summaries whose chunk starts inside a block instead of cutting there", () => {
    const segments = segmentOriginalByChunks(DOC, [
      chunk(0, "# Jude (King James Version) **Jude** — New Testament"),
      // A soft-wrapped continuation line: a line start, but not a block start.
      chunk(1, "sanctified by God the Father."),
    ]);

    expect(segments).toHaveLength(1);
    expect(segments[0].summaries.map((s) => s.chunkOrder)).toEqual([0, 1]);
    expect(segments[0].text).toBe(DOC);
  });

  it("never cuts inside a fenced code block", () => {
    const doc = ["# Title", "", "```md", "## Not a heading", "", "text", "```", ""].join("\n");
    const segments = segmentOriginalByChunks(doc, [
      chunk(0, "# Title"),
      chunk(1, "## Not a heading text"),
    ]);

    expect(segments).toHaveLength(1);
    expect(segments[0].summaries.map((s) => s.chunkOrder)).toEqual([0, 1]);
  });

  it("attaches summaries of unmatched chunks to the current segment", () => {
    const segments = segmentOriginalByChunks(DOC, [
      chunk(0, "# Jude (King James Version) **Jude** — New Testament"),
      chunk(1, "Text that does not appear anywhere in this document at all."),
      chunk(2, "## Closing 24 Now unto him that is able to keep you from falling."),
    ]);

    expect(segments).toHaveLength(2);
    expect(segments[0].summaries.map((s) => s.chunkOrder)).toEqual([0, 1]);
    expect(segments[1].summaries.map((s) => s.chunkOrder)).toEqual([2]);
  });

  it("does not move backwards when a later chunk matches earlier text", () => {
    const segments = segmentOriginalByChunks(DOC, [
      chunk(0, "## Closing 24 Now unto him that is able to keep you from falling."),
      chunk(1, "# Jude (King James Version) **Jude** — New Testament"),
    ]);

    expect(segments.map((s) => s.summaries.map((x) => x.chunkOrder))).toEqual([[], [0, 1]]);
    expect(segments.map((s) => s.text).join("")).toBe(DOC);
  });

  it("returns no segments for an empty original", () => {
    expect(segmentOriginalByChunks("   \n ", [chunk(0, "anything")])).toEqual([]);
  });

  it("keeps the whole document as one segment when there are no chunks", () => {
    const segments = segmentOriginalByChunks(DOC, []);
    expect(segments).toHaveLength(1);
    expect(segments[0].text).toBe(DOC);
    expect(segments[0].summaries).toEqual([]);
  });
});
