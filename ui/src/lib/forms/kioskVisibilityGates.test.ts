import { describe, expect, it } from "vitest";
import { evaluate } from "./jsonLogic";

// The Class Display kiosk (a stored view, not code) shows exactly ONE section at a
// time by gating each one on the session's `display_mode`. These are its literal
// stored `visible_when` expressions; the test pins the evaluator semantics they rely
// on — `in` over a literal array, and `!` as the mutually-exclusive fallback — so a
// change to jsonLogic can't silently blank the room's screen mid-lesson.
const SLIDE = { "!": [{ in: [{ var: "display_mode" }, ["join", "question", "results"]] }] };
const JOIN = { "==": [{ var: "display_mode" }, "join"] };
const QUESTION = { "==": [{ var: "display_mode" }, "question"] };
const RESULTS = { "==": [{ var: "display_mode" }, "results"] };

const SECTIONS = { slide: SLIDE, join: JOIN, question: QUESTION, results: RESULTS };

function visibleSections(displayMode: unknown): string[] {
  return Object.entries(SECTIONS)
    .filter(([, gate]) => evaluate(gate, { display_mode: displayMode }) === true)
    .map(([name]) => name);
}

describe("class display shows one thing at a time", () => {
  it.each([
    ["slide", "slide"],
    ["join", "join"],
    ["question", "question"],
    ["results", "results"],
  ])("display_mode=%s shows only the %s section", (mode, expected) => {
    expect(visibleSections(mode)).toEqual([expected]);
  });

  it("falls back to the slide when display_mode is unset", () => {
    // A blank screen mid-lesson would be worse than the wrong panel, so anything
    // unrecognised has to land on the slide rather than on nothing.
    expect(visibleSections(null)).toEqual(["slide"]);
    expect(visibleSections(undefined)).toEqual(["slide"]);
    expect(visibleSections("something_new")).toEqual(["slide"]);
  });

  it("never shows two sections at once for any value", () => {
    for (const mode of ["slide", "join", "question", "results", null, "", "JOIN", 0]) {
      expect(visibleSections(mode).length).toBe(1);
    }
  });
});
