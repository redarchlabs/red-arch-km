import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ChoicesSpec } from "@/lib/forms/puzzleSpec";

import { ChoicesPad } from "./ChoicesPad";

const spec: ChoicesSpec = {
  kind: "choices",
  columns: 1,
  options: [
    { value: "A", label: "Because she was queen" },
    { value: "B", label: "For such a time as this" },
    { value: "C", label: "She drew lots" },
  ],
};

function renderPad(props: Partial<React.ComponentProps<typeof ChoicesPad>> = {}) {
  return render(
    <ChoicesPad
      spec={spec}
      disabled={false}
      submitLabel="Send"
      submit={vi.fn()}
      picked={null}
      correct={null}
      {...props}
    />,
  );
}

/** The tile a label sits on — what carries the visual state. */
function tileFor(label: string): HTMLElement {
  const el = screen.getByText(label).closest("button");
  if (!el) throw new Error(`no tile for ${label}`);
  return el;
}

describe("ChoicesPad", () => {
  it("puts the option text on the tap target itself", () => {
    // The whole reason a phone doesn't have to scroll: the option and the way you
    // choose it are one object, not a list plus a separate row of letter buttons.
    renderPad();
    expect(tileFor("For such a time as this").tagName).toBe("BUTTON");
  });

  it("keeps showing what this person picked", () => {
    renderPad({ picked: "B" });
    expect(tileFor("For such a time as this").className).toContain("border-primary");
    expect(tileFor("Because she was queen").className).toContain("border-border");
  });

  it("gives away nothing before the answer is published", () => {
    // `correct: null` is the state for the whole time answering is open.
    renderPad({ picked: "B" });
    for (const opt of spec.options) {
      expect(tileFor(opt.label).className).not.toContain("border-green-500");
    }
  });

  it("marks the right answer and the wrong pick once revealed", () => {
    renderPad({ picked: "A", correct: "B" });
    expect(tileFor("For such a time as this").className).toContain("border-green-500");
    expect(tileFor("Because she was queen").className).toContain("border-destructive");
    expect(tileFor("She drew lots").className).toContain("border-dashed");
  });

  it("marks a correct pick once, not as both right and wrong", () => {
    renderPad({ picked: "B", correct: "B" });
    const tile = tileFor("For such a time as this");
    expect(tile.className).toContain("border-green-500");
    expect(tile.className).not.toContain("border-destructive");
  });

  it("reveals the answer even to someone who never answered", () => {
    renderPad({ picked: null, correct: "B" });
    expect(tileFor("For such a time as this").className).toContain("border-green-500");
  });

  it("does not submit while disabled", () => {
    const submit = vi.fn();
    renderPad({ submit, disabled: true });
    tileFor("She drew lots").click();
    expect(submit).not.toHaveBeenCalled();
  });

  it("submits the option's value, never its label", () => {
    // A workflow grades against the value; sending the label would break every
    // question whose wording is edited after the fact.
    const submit = vi.fn();
    renderPad({ submit });
    tileFor("She drew lots").click();
    expect(submit).toHaveBeenCalledWith({ solved: null, answer: "C" });
  });
});
