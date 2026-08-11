import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { FormRender } from "@/lib/api/forms";

import { FormRenderer } from "./FormRenderer";

const listRecords = vi.fn();

vi.mock("@/lib/api/entityRecords", async (importOriginal) => {
  const mod = await importOriginal<typeof import("@/lib/api/entityRecords")>();
  return { ...mod, listRecords: (...args: unknown[]) => listRecords(...args) };
});

/**
 * A row is often a thing you do SEVERAL things to — greet this person, or take them
 * off the register — and a list with one row button cannot say that. The failure this
 * guards against is quiet: `row_workflow_id` must keep behaving exactly as it always
 * did, because every existing list in every org depends on it.
 */
function makeRender(extra: Record<string, unknown>): FormRender {
  return {
    form_id: "v1",
    form_name: "Roster",
    description: null,
    status: "editable",
    root_entity_id: null,
    record_id: null,
    config: {
      version: 2,
      elements: [
        {
          id: "roster",
          type: "record_list",
          entity: "class_participant",
          fields: ["display_name"],
          ...extra,
        },
      ],
    },
    catalog: [],
    relationships: [],
    values: {},
    related: {},
  } as unknown as FormRender;
}

describe("record_list rows with more than one action", () => {
  beforeEach(() => {
    listRecords.mockReset();
    listRecords.mockResolvedValue({ items: [{ id: "p1", display_name: "Jeremy" }] });
  });

  it("draws the original row button and the extra ones side by side", async () => {
    render(
      <FormRenderer
        render={makeRender({
          row_workflow_id: "wf-remove",
          row_action_label: "Remove",
          row_actions: [{ workflow_id: "wf-greet", label: "Greet" }],
        })}
        mode="fill"
        viewContext
      />,
    );

    expect(await screen.findByRole("button", { name: "Remove" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Greet" })).toBeInTheDocument();
  });

  it("runs the workflow belonging to the button that was pressed", async () => {
    const onRunWorkflow = vi.fn().mockResolvedValue(undefined);
    render(
      <FormRenderer
        render={makeRender({
          row_workflow_id: "wf-remove",
          row_action_label: "Remove",
          row_actions: [
            { workflow_id: "wf-greet", label: "Greet", inputs: { who: { var: "id" } } },
          ],
        })}
        mode="fill"
        viewContext
        onRunWorkflow={onRunWorkflow}
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Greet" }));
    await waitFor(() => expect(onRunWorkflow).toHaveBeenCalled());
    expect(onRunWorkflow).toHaveBeenCalledWith("wf-greet", { who: "p1" }, "p1");
  });

  it("still works for a list that only declares the original single button", async () => {
    const onRunWorkflow = vi.fn().mockResolvedValue(undefined);
    render(
      <FormRenderer
        render={makeRender({
          row_workflow_id: "wf-remove",
          row_action_label: "Remove",
          row_workflow_inputs: { participant_id: { var: "id" } },
        })}
        mode="fill"
        viewContext
        onRunWorkflow={onRunWorkflow}
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Remove" }));
    await waitFor(() => expect(onRunWorkflow).toHaveBeenCalled());
    expect(onRunWorkflow).toHaveBeenCalledWith("wf-remove", { participant_id: "p1" }, "p1");
  });

  it("hides an action whose rule says no, showing its hidden text instead", async () => {
    render(
      <FormRenderer
        render={makeRender({
          row_actions: [
            {
              workflow_id: "wf-greet",
              label: "Greet",
              visible_when: { "!!": [{ var: "present" }] },
              hidden_text: "Away",
            },
          ],
        })}
        mode="fill"
        viewContext
      />,
    );

    expect(await screen.findByText("Away")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Greet" })).not.toBeInTheDocument();
  });
});
