import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { FormRender } from "@/lib/api/forms";

import { FormRenderer } from "./FormRenderer";

const listRecords = vi.fn();

vi.mock("@/lib/api/entityRecords", async (importOriginal) => {
  const mod = await importOriginal<typeof import("@/lib/api/entityRecords")>();
  return { ...mod, listRecords: (...args: unknown[]) => listRecords(...args) };
});

// The catalog shape: rows are courses; a lookup fetches the caller's enrollments
// plucked to course ids; the Enroll button hides for enrolled rows.
const ENROLL_RULE = { "!": { in: [{ var: "id" }, { var: "lookups.my_course_ids" }] } };

function makeRender(): FormRender {
  return {
    form_id: "v1",
    form_name: "Catalog",
    description: null,
    status: "editable",
    root_entity_id: null,
    record_id: null,
    config: {
      version: 2,
      elements: [
        {
          id: "list",
          type: "record_list",
          entity: "course",
          fields: ["title"],
          row_workflow_id: "wf-enroll",
          row_action_label: "Enroll",
          row_lookups: [
            {
              key: "my_course_ids",
              entity: "enrollment",
              filters: [{ field: "learner", op: "eq", value: "@me" }],
              pluck: "course",
            },
          ],
          row_workflow_visible_when: ENROLL_RULE,
          row_workflow_hidden_text: "Enrolled ✓",
        },
      ],
    },
    catalog: [],
    relationships: [],
    values: {},
    related: {},
  } as unknown as FormRender;
}

beforeEach(() => {
  listRecords.mockReset();
  listRecords.mockImplementation((entity: string) => {
    if (entity === "course") {
      return Promise.resolve({
        items: [
          { id: "course-a", title: "Security" },
          { id: "course-b", title: "Privacy" },
        ],
      });
    }
    // The lookup: caller is enrolled in course-a only.
    return Promise.resolve({ items: [{ id: "e1", course: "course-a" }] });
  });
});

describe("record_list per-row visibility", () => {
  it("hides the row action for rows matching the rule and shows the hidden text", async () => {
    render(<FormRenderer render={makeRender()} mode="fill" viewContext onRunWorkflow={vi.fn()} />);

    // Enrolled row (course-a): no button, hidden text instead.
    expect(await screen.findByText("Enrolled ✓")).toBeInTheDocument();
    // Unenrolled row (course-b): the button is there — exactly one.
    await waitFor(() => expect(screen.getAllByRole("button", { name: "Enroll" })).toHaveLength(1));
  });

  it("shows every row action when no rule is configured", async () => {
    const r = makeRender();
    const el = (r.config.elements as unknown as Array<Record<string, unknown>>)[0]!;
    delete el.row_workflow_visible_when;
    delete el.row_lookups;
    delete el.row_workflow_hidden_text;
    render(<FormRenderer render={r} mode="fill" viewContext onRunWorkflow={vi.fn()} />);

    await waitFor(() => expect(screen.getAllByRole("button", { name: "Enroll" })).toHaveLength(2));
  });
});
