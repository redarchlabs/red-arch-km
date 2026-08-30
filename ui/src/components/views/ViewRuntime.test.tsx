import { fireEvent, render as rtlRender, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ViewRuntime } from "./ViewRuntime";

const getViewRender = vi.fn();
const runWorkflow = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), back: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

// A signed-in kiosk with its org already resolved. The runtime waits for that
// before fetching, because the org header is read from storage by the axios
// interceptor and is not there until /users/me returns.
const orgState = { currentOrg: null, currentOrgId: "o1" as string | null, isLoading: false };

vi.mock("@/context/OrgContext", () => ({
  useOrg: () => orgState,
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
  orgState.currentOrgId = "o1";
  orgState.isLoading = false;
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

  it("gives a synchronous run far longer than the client's default timeout", async () => {
    // A manual run executes INLINE: the request is open for as long as the workflow takes.
    // A robot presentation pre-renders minutes of speech before its one step returns — well
    // past the 30s client default, which surfaced as "timeout of 30000ms exceeded" on a run
    // that was in fact succeeding.
    getViewRender.mockResolvedValue(makeRender("initial"));
    runWorkflow.mockResolvedValue({ run_id: "r1", status: "succeeded", error: null });

    rtlRender(<ViewRuntime id="v1" />);
    fireEvent.click(await screen.findByRole("button", { name: "trigger-run" }));

    await waitFor(() => expect(runWorkflow).toHaveBeenCalled());
    const timeoutMs = runWorkflow.mock.calls[0][2];
    expect(timeoutMs).toBeGreaterThanOrEqual(180_000);
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

describe("ViewRuntime org gating", () => {
  // A deep link opened in a cold browser used to render "X-Org-ID header is
  // required": the fetch fired before OrgContext had resolved the org into
  // storage, where the axios interceptor reads it from.
  it("does not fetch until the org is resolved", async () => {
    orgState.isLoading = true;
    orgState.currentOrgId = null;
    getViewRender.mockResolvedValue(makeRender("first"));

    rtlRender(<ViewRuntime id="v1" kiosk />);

    await waitFor(() => expect(getViewRender).not.toHaveBeenCalled());
  });

  it("fetches once the org lands", async () => {
    orgState.isLoading = false;
    orgState.currentOrgId = "o1";
    getViewRender.mockResolvedValue(makeRender("first"));

    rtlRender(<ViewRuntime id="v1" kiosk />);

    await waitFor(() => expect(getViewRender).toHaveBeenCalled());
  });
});
