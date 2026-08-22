import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { FormElement, FormRender } from "@/lib/api/forms";

import { FormRenderer } from "./FormRenderer";

function makeRender(elements: FormElement[]): FormRender {
  return {
    form_id: "v1",
    form_name: "Console",
    description: null,
    status: "editable",
    root_entity_id: null,
    record_id: null,
    config: { version: 2, elements },
    catalog: [],
    relationships: [],
    values: {},
    related: {},
  } as unknown as FormRender;
}

function draw(elements: FormElement[]) {
  render(<FormRenderer render={makeRender(elements)} mode="fill" viewContext onRunWorkflow={vi.fn()} />);
}

const text = (t: string) => ({ type: "label", text: t }) as unknown as FormElement;

const tabs = (over: Record<string, unknown> = {}) =>
  ({
    id: "t1",
    type: "tab_group",
    tabs: [
      { label: "Motion", elements: [text("pose controls")] },
      { label: "Voice", elements: [text("say something")] },
      { label: "Senses", elements: [text("sonar")] },
    ],
    ...over,
  }) as unknown as FormElement;

const accordion = (over: Record<string, unknown> = {}) =>
  ({
    id: "a1",
    type: "accordion",
    panes: [
      { label: "Arms", elements: [text("shoulder pitch")] },
      { label: "Presentations", elements: [text("laser show")] },
    ],
    ...over,
  }) as unknown as FormElement;

describe("tab_group", () => {
  it("opens on the first tab and shows only that tab's children", () => {
    draw([tabs()]);
    expect(screen.getByText("pose controls")).toBeInTheDocument();
    expect(screen.queryByText("say something")).not.toBeInTheDocument();
  });

  it("honours default_tab", () => {
    draw([tabs({ default_tab: 1 })]);
    expect(screen.getByText("say something")).toBeInTheDocument();
    expect(screen.queryByText("pose controls")).not.toBeInTheDocument();
  });

  // Deleting a tab leaves the saved default pointing past the end. The screen
  // must still render something rather than an empty body.
  it("clamps a default_tab that no longer names a tab", () => {
    draw([tabs({ default_tab: 9 })]);
    expect(screen.getByText("sonar")).toBeInTheDocument();
  });

  it("switches on click", () => {
    draw([tabs()]);
    fireEvent.click(screen.getByRole("tab", { name: "Voice" }));
    expect(screen.getByText("say something")).toBeInTheDocument();
    expect(screen.queryByText("pose controls")).not.toBeInTheDocument();
  });

  it("moves through the strip with the arrow keys and wraps", () => {
    draw([tabs()]);
    fireEvent.keyDown(screen.getByRole("tab", { name: "Motion" }), { key: "ArrowRight" });
    expect(screen.getByText("say something")).toBeInTheDocument();
    // Left from the first tab wraps to the last rather than dead-ending.
    fireEvent.keyDown(screen.getByRole("tab", { name: "Voice" }), { key: "ArrowLeft" });
    fireEvent.keyDown(screen.getByRole("tab", { name: "Motion" }), { key: "ArrowLeft" });
    expect(screen.getByText("sonar")).toBeInTheDocument();
  });

  it("marks the selected tab for assistive tech and keeps one stop in the tab order", () => {
    draw([tabs({ default_tab: 1 })]);
    expect(screen.getByRole("tab", { name: "Voice" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Motion" })).toHaveAttribute("tabindex", "-1");
    expect(screen.getByRole("tab", { name: "Voice" })).toHaveAttribute("tabindex", "0");
  });

  // The renderer used to key its open/closed state off `el.id ?? "tabs"`, so two
  // id-less groups shared one slot and switched in lockstep.
  it("keeps two id-less tab groups independent", () => {
    const a = tabs({ id: undefined });
    const b = tabs({
      id: undefined,
      tabs: [
        { label: "Left", elements: [text("left arm")] },
        { label: "Right", elements: [text("right arm")] },
      ],
    });
    draw([a, b]);
    fireEvent.click(screen.getByRole("tab", { name: "Right" }));
    expect(screen.getByText("right arm")).toBeInTheDocument();
    // The first group must not have moved off its own first tab.
    expect(screen.getByText("pose controls")).toBeInTheDocument();
  });
});

describe("accordion", () => {
  it("opens the first pane by default, like it always did", () => {
    draw([accordion()]);
    expect(screen.getByText("shoulder pitch")).toBeInTheDocument();
    expect(screen.queryByText("laser show")).not.toBeInTheDocument();
  });

  // The bug: clicking the open header re-set the same index, so the header was
  // inert and the stack could never be fully collapsed.
  it("closes the open pane when its header is clicked again", () => {
    draw([accordion()]);
    fireEvent.click(screen.getByRole("button", { name: /Arms/ }));
    expect(screen.queryByText("shoulder pitch")).not.toBeInTheDocument();
  });

  it("closes the previous pane when panes are exclusive", () => {
    draw([accordion()]);
    fireEvent.click(screen.getByRole("button", { name: /Presentations/ }));
    expect(screen.getByText("laser show")).toBeInTheDocument();
    expect(screen.queryByText("shoulder pitch")).not.toBeInTheDocument();
  });

  it("holds both panes open when multi is set", () => {
    draw([accordion({ multi: true })]);
    fireEvent.click(screen.getByRole("button", { name: /Presentations/ }));
    expect(screen.getByText("laser show")).toBeInTheDocument();
    expect(screen.getByText("shoulder pitch")).toBeInTheDocument();
  });

  it("can start fully collapsed", () => {
    draw([accordion({ default_open: [] })]);
    expect(screen.queryByText("shoulder pitch")).not.toBeInTheDocument();
    expect(screen.queryByText("laser show")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Arms/ })).toHaveAttribute("aria-expanded", "false");
  });

  it("can start with several panes open", () => {
    draw([accordion({ multi: true, default_open: [0, 1] })]);
    expect(screen.getByText("shoulder pitch")).toBeInTheDocument();
    expect(screen.getByText("laser show")).toBeInTheDocument();
  });

  it("drops a default_open index that no longer names a pane", () => {
    draw([accordion({ multi: true, default_open: [1, 7] })]);
    expect(screen.getByText("laser show")).toBeInTheDocument();
    expect(screen.queryByText("shoulder pitch")).not.toBeInTheDocument();
  });
});

describe("tabs holding accordions", () => {
  it("renders a nested stack and keeps the closed tab's panes unmounted", () => {
    draw([
      tabs({
        tabs: [
          { label: "Motion", elements: [accordion()] },
          { label: "Voice", elements: [text("say something")] },
        ],
      }),
    ]);
    expect(screen.getByText("shoulder pitch")).toBeInTheDocument();

    // Switching away unmounts the whole nested stack — that is what makes a tab
    // a cheap place to park a polling element.
    fireEvent.click(screen.getByRole("tab", { name: "Voice" }));
    expect(screen.queryByRole("button", { name: /Arms/ })).not.toBeInTheDocument();
  });
});

// Inputs are seeded with their authored defaults once at mount, from a walk over
// the whole tree — closed tabs and panes included. That is what lets a control in
// one tab feed a workflow button in another.
describe("input defaults inside containers", () => {
  const slider = (key: string, def: number) =>
    ({ type: "input", key, label: key, control: "number", default: def }) as unknown as FormElement;

  const seeded = (el: FormElement) => {
    draw([el]);
    return screen.getAllByRole("spinbutton").map((n) => (n as HTMLInputElement).value);
  };

  it("seeds an input sitting in a closed accordion pane", () => {
    draw([
      accordion({
        panes: [
          { label: "Open", elements: [slider("a", 1)] },
          { label: "Closed", elements: [slider("b", 2)] },
        ],
      }),
    ]);
    // Only the open pane renders, so only one control is on screen...
    expect(screen.getAllByRole("spinbutton")).toHaveLength(1);
    // ...and opening the other finds its default already in place, not blank.
    fireEvent.click(screen.getByRole("button", { name: /Closed/ }));
    expect((screen.getByRole("spinbutton") as HTMLInputElement).value).toBe("2");
  });

  // `card` was missing from the client-side walk while the server counted it as a
  // layout container, so an input in a dashboard tile lost its default.
  it("seeds an input inside a card", () => {
    expect(
      seeded({ id: "c1", type: "card", title: "Tile", elements: [slider("a", 7)] } as unknown as FormElement),
    ).toEqual(["7"]);
  });

  it("seeds an input inside a panel", () => {
    expect(
      seeded({ id: "p1", type: "panel", title: "Panel", elements: [slider("a", 7)] } as unknown as FormElement),
    ).toEqual(["7"]);
  });
});
