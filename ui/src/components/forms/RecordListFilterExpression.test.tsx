import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { FormRender } from "@/lib/api/forms";

import { FormRenderer } from "./FormRenderer";

const listRecords = vi.fn();

vi.mock("@/lib/api/entityRecords", async (importOriginal) => {
  const mod = await importOriginal<typeof import("@/lib/api/entityRecords")>();
  return { ...mod, listRecords: (...args: unknown[]) => listRecords(...args) };
});

/**
 * A board filtered by a picker elsewhere on the page: the lesson-plan view shows
 * the segments of whichever lesson is selected, rather than needing a view per week.
 *
 * The dangerous failure here is silent: if an unresolved expression simply dropped
 * its filter, the fetch would go out UNFILTERED and the board would show every
 * lesson's segments interleaved — which looks like data corruption, not like an
 * empty state.
 */
function makeRender(defaultWeek: string | null): FormRender {
  return {
    form_id: "v1",
    form_name: "Lesson plan",
    description: null,
    status: "editable",
    root_entity_id: null,
    record_id: null,
    config: {
      version: 2,
      elements: [
        {
          id: "wk",
          type: "input",
          key: "week",
          control: "select",
          label: "Lesson",
          default: defaultWeek,
          options: [{ value: "33" }, { value: "32" }],
        },
        {
          id: "segs",
          type: "record_list",
          entity: "lesson_segment",
          fields: ["title"],
          filters: [{ field: "lesson_week", op: "eq", value: { var: "week" } }],
        },
      ],
    },
    catalog: [],
    relationships: [],
    values: {},
    related: {},
  } as unknown as FormRender;
}

describe("record_list filters driven by an expression", () => {
  beforeEach(() => {
    listRecords.mockReset();
    listRecords.mockResolvedValue({ items: [{ id: "s1", title: "Welcome and hook" }] });
  });

  it("resolves the expression against the view's values before fetching", async () => {
    render(<FormRenderer render={makeRender("33")} mode="fill" viewContext />);

    await waitFor(() => expect(listRecords).toHaveBeenCalled());
    const [entity, params] = listRecords.mock.calls.at(-1)!;
    expect(entity).toBe("lesson_segment");
    expect(params.filters).toEqual([{ field: "lesson_week", op: "eq", value: "33" }]);
  });

  it("does not fetch at all while the expression resolves to nothing", async () => {
    // The whole point: no selection must not become "fetch everything".
    render(<FormRenderer render={makeRender(null)} mode="fill" viewContext />);

    await screen.findByText("Make a selection to see these.");
    expect(listRecords).not.toHaveBeenCalled();
  });

  it("says nothing is chosen rather than claiming there are no records", async () => {
    render(<FormRenderer render={makeRender(null)} mode="fill" viewContext />);

    expect(await screen.findByText("Make a selection to see these.")).toBeInTheDocument();
    expect(screen.queryByText("No records yet.")).not.toBeInTheDocument();
  });

  it("still passes a literal value straight through", async () => {
    const r = makeRender("33");
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (r.config.elements[1] as any).filters = [{ field: "lesson_week", op: "eq", value: 32 }];

    render(<FormRenderer render={r} mode="fill" viewContext />);

    await waitFor(() => expect(listRecords).toHaveBeenCalled());
    const [, params] = listRecords.mock.calls.at(-1)!;
    expect(params.filters).toEqual([{ field: "lesson_week", op: "eq", value: "32" }]);
  });
});
