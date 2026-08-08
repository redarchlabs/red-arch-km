import { describe, expect, it } from "vitest";

import { type ReaderBlockChunk, buildReaderBlocks } from "./readerBlocks";

function chunk(order: number, text: string, summary = `s${order}`): ReaderBlockChunk {
  return { chunk_order: order, text, summary };
}

describe("buildReaderBlocks", () => {
  it("keeps a single chunk as-is", () => {
    const blocks = buildReaderBlocks([chunk(0, "One sentence. And another.")]);
    expect(blocks).toEqual([
      { chunkOrder: 0, summary: "s0", text: "One sentence. And another." },
    ]);
  });

  it("moves the tail of a split sentence onto the block that started it", () => {
    // The real seam from an OCR'd PDF: the chunker cut at an extracted line end.
    const blocks = buildReaderBlocks([
      chunk(0, "and Achim begot Eliud; and Eliud begot"),
      chunk(1, "Eleazar; and Eleazar begot Matthan. 17] the generations, therefore."),
    ]);

    expect(blocks[0].text).toBe("and Achim begot Eliud; and Eliud begot Eleazar; and Eleazar begot Matthan.");
    expect(blocks[1].text).toBe("17] the generations, therefore.");
    expect(blocks.map((b) => b.chunkOrder)).toEqual([0, 1]);
  });

  it("leaves a seam alone when the previous chunk already ends a sentence", () => {
    const blocks = buildReaderBlocks([
      chunk(0, "He went up into the mountain."),
      chunk(1, "And his disciples came to him."),
    ]);

    expect(blocks[0].text).toBe("He went up into the mountain.");
    expect(blocks[1].text).toBe("And his disciples came to him.");
  });

  it("treats a closing quote after the terminator as a clean ending", () => {
    const blocks = buildReaderBlocks([
      chunk(0, 'He said: "Follow me."'),
      chunk(1, "Then they left their nets."),
    ]);

    expect(blocks[0].text).toBe('He said: "Follow me."');
    expect(blocks[1].text).toBe("Then they left their nets.");
  });

  it("falls back to a clause boundary when no sentence ends within the window", () => {
    const clause = "and Azor begot Zadock; ";
    const blocks = buildReaderBlocks([
      chunk(0, "and Eliakim begot"),
      chunk(1, `Azor; ${clause.repeat(40)}and it was finished.`),
    ]);

    expect(blocks[0].text).toBe("and Eliakim begot Azor;");
    expect(blocks[1].text.startsWith("and Azor begot Zadock;")).toBe(true);
  });

  it("leaves the seam untouched when nothing readable is close enough", () => {
    const runOn = `${"word ".repeat(400)}end.`;
    const blocks = buildReaderBlocks([chunk(0, "an unfinished line"), chunk(1, runOn)]);

    expect(blocks[0].text).toBe("an unfinished line");
    expect(blocks[1].text).toBe(runOn);
  });

  it("never empties the following block to complete a sentence", () => {
    const blocks = buildReaderBlocks([chunk(0, "an unfinished"), chunk(1, "line.")]);

    expect(blocks[0].text).toBe("an unfinished");
    expect(blocks[1].text).toBe("line.");
  });

  it("drops the chunker's overlap so the seam does not repeat itself", () => {
    const overlap = "The Letter of Paul to Philemon. Letter to the Hebrews.";
    const blocks = buildReaderBlocks([
      chunk(0, `The Letter of Paul to Titus. ${overlap}`),
      chunk(1, `${overlap} The General Letter of James.`),
    ]);

    expect(blocks[0].text).toBe(`The Letter of Paul to Titus. ${overlap}`);
    expect(blocks[1].text).toBe("The General Letter of James.");
  });

  it("drops a short repeat that ends on the sentence the previous block ends on", () => {
    const blocks = buildReaderBlocks([
      chunk(0, "every tree that brings not forth good fruit is cut down, and cast into the fire."),
      chunk(1, "the fire. I indeed immerse you in water unto repentance."),
    ]);

    expect(blocks[1].text).toBe("I indeed immerse you in water unto repentance.");
  });

  it("keeps a short opening sentence the previous block does not end with", () => {
    const blocks = buildReaderBlocks([
      chunk(0, "he cast the net into the sea."),
      chunk(1, "The boat. Then they sailed away."),
    ]);

    expect(blocks[1].text).toBe("The boat. Then they sailed away.");
  });

  it("ignores a short coincidental repeat rather than deleting real text", () => {
    const blocks = buildReaderBlocks([
      chunk(0, "and he said to them."),
      chunk(1, "And he said to them the parable of the sower."),
    ]);

    expect(blocks[1].text).toBe("And he said to them the parable of the sower.");
  });

  it("strips an overlap first, then completes the sentence from what is left", () => {
    const overlap = "which was spoken by the prophet, saying to the people of Judea";
    const blocks = buildReaderBlocks([
      chunk(0, `it might be fulfilled ${overlap}`),
      chunk(1, `${overlap} in that day. Now the birth of Jesus was thus.`),
    ]);

    expect(blocks[0].text).toBe(`it might be fulfilled ${overlap} in that day.`);
    expect(blocks[1].text).toBe("Now the birth of Jesus was thus.");
  });

  it("keeps a fully duplicated chunk as an empty block so its citation anchor survives", () => {
    const body = "The whole of this chunk repeats the previous one word for word.";
    const blocks = buildReaderBlocks([chunk(0, body), chunk(1, body)]);

    expect(blocks).toHaveLength(2);
    expect(blocks[1].chunkOrder).toBe(1);
    expect(blocks[1].text).toBe("");
  });

  it("collapses whitespace and normalizes a blank summary to null", () => {
    const blocks = buildReaderBlocks([chunk(0, "  spaced   out\n\ntext.  ", "   ")]);

    expect(blocks[0].text).toBe("spaced out text.");
    expect(blocks[0].summary).toBeNull();
  });

  it("returns nothing for no chunks", () => {
    expect(buildReaderBlocks([])).toEqual([]);
  });
});
