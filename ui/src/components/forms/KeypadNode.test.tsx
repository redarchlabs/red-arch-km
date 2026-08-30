import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { KeypadElement } from "@/lib/api/forms";

import { KeypadNode } from "./KeypadNode";

/**
 * A keypad's fixed inputs are JsonLogic, exactly as a button's are. The bug this
 * guards against was silent on the page and only visible at the far end: the
 * expression itself was sent to the workflow, which rejected it as "must be a
 * number" — with nothing on screen to say the layout was at fault.
 */
function pad(extra: Partial<KeypadElement> = {}): KeypadElement {
  return {
    type: "keypad",
    workflow_id: "wf-1",
    input_name: "heading",
    max_length: 3,
    ...extra,
  } as KeypadElement;
}

function type(digits: string) {
  for (const d of digits) fireEvent.click(screen.getByRole("button", { name: d }));
}

describe("KeypadNode inputs", () => {
  it("resolves an expression input against the view's values", async () => {
    const onRun = vi.fn();
    render(
      <KeypadNode
        el={pad({ inputs: { notch: { var: "pump_speed" } } })}
        values={{ pump_speed: 3 }}
        onRun={onRun}
      />
    );
    type("090");
    fireEvent.click(screen.getByRole("button", { name: "Enter" }));
    await waitFor(() => expect(onRun).toHaveBeenCalled());
    expect(onRun).toHaveBeenCalledWith("wf-1", { notch: 3, heading: 90 });
  });

  it("passes a literal input through unchanged", async () => {
    const onRun = vi.fn();
    render(<KeypadNode el={pad({ inputs: { notch: 2, mode: "manual" } })} values={{}} onRun={onRun} />);
    type("45");
    fireEvent.click(screen.getByRole("button", { name: "Enter" }));
    await waitFor(() => expect(onRun).toHaveBeenCalled());
    expect(onRun).toHaveBeenCalledWith("wf-1", { notch: 2, mode: "manual", heading: 45 });
  });

  it("sends the typed value as a number, under the element's input name", async () => {
    const onRun = vi.fn();
    render(<KeypadNode el={pad({ input_name: "bearing" })} values={{}} onRun={onRun} />);
    type("180");
    fireEvent.click(screen.getByRole("button", { name: "Enter" }));
    await waitFor(() => expect(onRun).toHaveBeenCalled());
    expect(onRun).toHaveBeenCalledWith("wf-1", { bearing: 180 });
  });

  it("does not run with nothing entered", () => {
    const onRun = vi.fn();
    render(<KeypadNode el={pad()} values={{}} onRun={onRun} />);
    expect(screen.getByRole("button", { name: "Enter" })).toBeDisabled();
  });
});
