import { fireEvent, render as rtlRender, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ViewRuntime } from "./ViewRuntime";

const getViewRender = vi.fn();
const runWorkflow = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), back: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("@/context/OrgContext", () => ({
  useOrg: () => ({ currentOrg: null }),
}));

vi.mock("@/lib/api/views", () => ({
  getViewRender: (...args: unknown[]) => getViewRender(...args),
  getPublicViewRender: vi.fn(),
  runPublicViewWorkflow: vi.fn(),
}));

vi.mock("@/lib/api/workflows", () => ({
  runWorkflow: (...args: unknown[]) => runWorkflow(...args),
}));

// The renderer is a heavyweight tree; all this test needs is a way to trigger
// the run callback the way a record-list row button would.
vi.mock("@/components/forms/FormRenderer", () => ({
  FormRenderer: ({ onRunWorkflow }: { onRunWorkflow?: (id: string, inputs: object) => void }) => (
    <button type="button" onClick={() => onRunWorkflow?.("wf-1", {})}>
      trigger-run
    </button>
  ),
}));

function makeRender(marker: string) {
  return {
    form_id: "v1",
    form_name: "View",
    description: marker,
    status: "editable",
    root_entity_id: null,
    record_id: null,
    config: { version: 2, elements: [] },
    catalog: [],
    relationships: [],
    values: {},
    related: {},
  };
}

beforeEach(() => {
  getViewRender.mockReset();
  runWorkflow.mockReset();
});

describe("ViewRuntime run feedback", () => {
  it("reports the run's outcome and re-fetches the render on success", async () => {
    getViewRender.mockResolvedValueOnce(makeRender("initial")).mockResolvedValueOnce(makeRender("after-run"));
    runWorkflow.mockResolvedValue({ run_id: "r1", status: "succeeded", error: null });

    rtlRender(<ViewRuntime id="v1" />);
    fireEvent.click(await screen.findByRole("button", { name: "trigger-run" }));

    // Outcome-aware notice, not the old fire-and-forget "Workflow started."
    expect(await screen.findByText("Done.")).toBeInTheDocument();
    // The page re-fetched so it reflects what the run changed.
    await waitFor(() => expect(getViewRender).toHaveBeenCalledTimes(2));
  });

  it("surfaces a failed run as an error", async () => {
    getViewRender.mockResolvedValue(makeRender("initial"));
    runWorkflow.mockResolvedValue({ run_id: "r1", status: "failed", error: "learner not found" });

    rtlRender(<ViewRuntime id="v1" />);
    fireEvent.click(await screen.findByRole("button", { name: "trigger-run" }));

    expect(await screen.findByText("learner not found")).toBeInTheDocument();
    expect(getViewRender).toHaveBeenCalledTimes(1);
  });
});
