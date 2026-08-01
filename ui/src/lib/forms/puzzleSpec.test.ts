import { describe, expect, it } from "vitest";

import {
  describeArrangement,
  fillSpecTokens,
  gradeColor,
  gradeSequence,
  gradeSort,
  gradeWires,
  isPuzzleKind,
  parsePuzzleSpec,
  seedFrom,
  stableShuffle,
  type ChoicesSpec,
  type ColorSpec,
  type SequenceSpec,
  type SortSpec,
  type WiresSpec,
} from "./puzzleSpec";

/** Narrow a parse result, failing the test with the parser's own reason when it
 * didn't parse — a bare non-null assertion would report "cannot read property of
 * undefined" three lines later instead of the actual complaint. */
function spec<T>(result: ReturnType<typeof parsePuzzleSpec>): T {
  if (!result.ok) throw new Error(`expected a parseable spec, got: ${result.error}`);
  return result.spec as T;
}

describe("parsePuzzleSpec", () => {
  it("accepts a spec as a JSON string, which is how a record field stores it", () => {
    const parsed = spec<ChoicesSpec>(
      parsePuzzleSpec("choices", '{"options":[{"value":"A","label":"Yes"},{"value":"B","label":"No"}]}'),
    );
    expect(parsed.options).toEqual([
      { value: "A", label: "Yes" },
      { value: "B", label: "No" },
    ]);
  });

  it("treats a bare string option as its own value and label", () => {
    const parsed = spec<ChoicesSpec>(parsePuzzleSpec("choices", { options: ["Red", "Blue"] }));
    expect(parsed.options).toEqual([
      { value: "Red", label: "Red" },
      { value: "Blue", label: "Blue" },
    ]);
  });

  it("keeps a numeric option value as a string so grading compares like with like", () => {
    const parsed = spec<ChoicesSpec>(
      parsePuzzleSpec("choices", { options: [{ value: 48, label: "48" }, { value: 12, label: "12" }] }),
    );
    expect(parsed.options[0].value).toBe("48");
  });

  it("refuses a choices puzzle that can't be answered", () => {
    const result = parsePuzzleSpec("choices", { options: [{ value: "A", label: "Only one" }] });
    expect(result.ok).toBe(false);
  });

  it("refuses malformed JSON rather than rendering an empty pad", () => {
    const result = parsePuzzleSpec("choices", "{not json");
    expect(result.ok).toBe(false);
  });

  it("defaults a sequence's order to the order the steps were written in", () => {
    const parsed = spec<SequenceSpec>(
      parsePuzzleSpec("sequence", { items: [{ label: "One" }, { label: "Two" }, { label: "Three" }] }),
    );
    expect(parsed.order).toEqual([0, 1, 2]);
  });

  it("rejects a sequence whose order doesn't line up with its steps", () => {
    const result = parsePuzzleSpec("sequence", {
      items: [{ label: "One" }, { label: "Two" }],
      order: [0, 0],
    });
    expect(result.ok).toBe(false);
  });

  it("gives wires distinct default colours so two leads never look alike", () => {
    const parsed = spec<WiresSpec>(
      parsePuzzleSpec("wires", {
        pairs: [
          { left: "A", right: "1" },
          { left: "B", right: "2" },
          { left: "C", right: "3" },
        ],
      }),
    );
    expect(new Set(parsed.pairs.map((p) => p.color)).size).toBe(3);
  });

  it("resolves a sort item's bin by name as well as by index", () => {
    const parsed = spec<SortSpec>(
      parsePuzzleSpec("sort", {
        bins: [{ label: "Recycle" }, { label: "Jettison" }],
        items: [
          { label: "Water", bin: "Recycle" },
          { label: "Scrap", bin: 1 },
        ],
      }),
    );
    expect(parsed.items.map((i) => i.bin)).toEqual([0, 1]);
  });

  it("refuses a sort item that isn't assigned to any bin", () => {
    const result = parsePuzzleSpec("sort", {
      bins: [{ label: "Recycle" }],
      items: [{ label: "Water", bin: "Nowhere" }],
    });
    expect(result.ok).toBe(false);
  });

  it("resolves a colour target by palette name", () => {
    const parsed = spec<ColorSpec>(
      parsePuzzleSpec("color", {
        palette: [
          { name: "Red", color: "#f00" },
          { name: "Blue", color: "#00f" },
        ],
        regions: [{ label: "Nose", target: "Blue" }],
      }),
    );
    expect(parsed.regions[0].target).toBe(1);
  });

  it("ignores targets entirely in free-paint mode", () => {
    const parsed = spec<ColorSpec>(
      parsePuzzleSpec("color", {
        palette: ["red", "blue"],
        regions: [{ label: "Nose" }, { label: "Wings" }],
        free: true,
      }),
    );
    expect(parsed.free).toBe(true);
    expect(parsed.regions.map((r) => r.target)).toEqual([-1, -1]);
  });

  it("refuses a colour target that isn't in the palette", () => {
    const result = parsePuzzleSpec("color", {
      palette: ["red"],
      regions: [{ label: "Nose", target: "chartreuse" }],
    });
    expect(result.ok).toBe(false);
  });

  it("keeps the keypad's answer out of the spec entirely", () => {
    // The pad is never told the answer for keypad/choices — the parsed spec only
    // ever describes the KEYS, so nothing about it can leak a correct value.
    const parsed = parsePuzzleSpec("keypad", { max_len: 3, units: "%", answer: "48" });
    expect(parsed.ok).toBe(true);
    expect(JSON.stringify(parsed)).not.toContain("48");
  });
});

describe("grading", () => {
  const sequence = spec<SequenceSpec>(
    parsePuzzleSpec("sequence", {
      items: [{ label: "Seal" }, { label: "Pressurise" }, { label: "Open" }],
    }),
  );

  it("passes a sequence tapped in the required order", () => {
    expect(gradeSequence(sequence, [0, 1, 2])).toBe(true);
  });

  it("fails a sequence in the wrong order", () => {
    expect(gradeSequence(sequence, [1, 0, 2])).toBe(false);
  });

  it("fails an unfinished sequence rather than passing a prefix", () => {
    expect(gradeSequence(sequence, [0, 1])).toBe(false);
  });

  const wires = spec<WiresSpec>(
    parsePuzzleSpec("wires", {
      pairs: [
        { left: "Reactor", right: "Core" },
        { left: "Antenna", right: "Relay" },
      ],
    }),
  );

  it("passes wires where each lead reaches its own port", () => {
    expect(gradeWires(wires, [0, 1])).toBe(true);
  });

  it("fails crossed wires", () => {
    expect(gradeWires(wires, [1, 0])).toBe(false);
  });

  it("fails a console with a lead still hanging loose", () => {
    expect(gradeWires(wires, [0, -1])).toBe(false);
  });

  const sort = spec<SortSpec>(
    parsePuzzleSpec("sort", {
      bins: [{ label: "Recycle" }, { label: "Jettison" }],
      items: [
        { label: "Water", bin: 0 },
        { label: "Scrap", bin: 1 },
      ],
    }),
  );

  it("passes items sorted into their bins", () => {
    expect(gradeSort(sort, [0, 1])).toBe(true);
  });

  it("fails an item left in the tray", () => {
    expect(gradeSort(sort, [0, -1])).toBe(false);
  });

  const color = spec<ColorSpec>(
    parsePuzzleSpec("color", {
      palette: [
        { name: "Red", color: "#f00" },
        { name: "Blue", color: "#00f" },
      ],
      regions: [
        { label: "Nose", target: "Red" },
        { label: "Wings", target: "Blue" },
      ],
    }),
  );

  it("passes a picture painted to match", () => {
    expect(gradeColor(color, [0, 1])).toBe(true);
  });

  it("fails a mismatched fill", () => {
    expect(gradeColor(color, [1, 1])).toBe(false);
  });

  it("accepts any colour in free-paint mode, but still wants every region filled", () => {
    const free = spec<ColorSpec>(
      parsePuzzleSpec("color", {
        palette: ["red", "blue"],
        regions: [{ label: "Nose" }, { label: "Wings" }],
        free: true,
      }),
    );
    expect(gradeColor(free, [1, 1])).toBe(true);
    expect(gradeColor(free, [1, -1])).toBe(false);
  });
});

describe("describeArrangement", () => {
  it("reports a sequence by step name, not by index", () => {
    const sequence = spec<SequenceSpec>(
      parsePuzzleSpec("sequence", { items: [{ label: "Seal" }, { label: "Open" }] }),
    );
    expect(describeArrangement(sequence, [1, 0])).toBe("Open → Seal");
  });

  it("names both ends of each wire", () => {
    const wires = spec<WiresSpec>(
      parsePuzzleSpec("wires", {
        pairs: [
          { left: "Reactor", right: "Core" },
          { left: "Antenna", right: "Relay" },
        ],
      }),
    );
    expect(describeArrangement(wires, [1, 0])).toBe("Reactor→Relay, Antenna→Core");
  });
});

describe("stableShuffle", () => {
  it("is a permutation — every item is still there exactly once", () => {
    const out = stableShuffle(6, seedFrom("a-puzzle"));
    expect([...out].sort((a, b) => a - b)).toEqual([0, 1, 2, 3, 4, 5]);
  });

  it("returns the same arrangement for the same seed, so a redraw doesn't move targets", () => {
    expect(stableShuffle(8, seedFrom("same"))).toEqual(stableShuffle(8, seedFrom("same")));
  });

  it("gives different puzzles different arrangements", () => {
    expect(stableShuffle(8, seedFrom("one"))).not.toEqual(stableShuffle(8, seedFrom("two")));
  });
});

describe("isPuzzleKind", () => {
  it("accepts the known kinds and rejects anything else", () => {
    expect(isPuzzleKind("wires")).toBe(true);
    expect(isPuzzleKind("crossword")).toBe(false);
    expect(isPuzzleKind(null)).toBe(false);
  });
});

describe("fillSpecTokens", () => {
  it("fills a {slug} placeholder from the record, so one spec serves many records", () => {
    const filled = fillSpecTokens('{"options":[{"value":"A","label":"{choice_a}"}]}', {
      choice_a: "48 kilograms",
    });
    expect(JSON.parse(filled).options[0].label).toBe("48 kilograms");
  });

  it("escapes a value so a quote in the data cannot rewrite the spec's structure", () => {
    const filled = fillSpecTokens('{"options":[{"value":"A","label":"{choice_a}"}]}', {
      choice_a: 'the "big" one\\',
    });
    expect(JSON.parse(filled).options[0].label).toBe('the "big" one\\');
  });

  it("leaves the spec's own JSON structure alone", () => {
    const json = '{"options":[{"value":"A","label":"Yes"}]}';
    expect(fillSpecTokens(json, {})).toBe(json);
  });

  it("ignores braces that aren't shaped like a field slug", () => {
    const json = '{"regions":[{"label":"{Red}"},{"label":"{1}"}]}';
    expect(fillSpecTokens(json, { Red: "nope" })).toBe(json);
  });

  it("blanks a placeholder the record has nothing for, rather than showing the raw token", () => {
    const filled = fillSpecTokens('{"options":[{"value":"A","label":"{choice_d}"}]}', { choice_a: "x" });
    expect(JSON.parse(filled).options[0].label).toBe("");
  });
});
