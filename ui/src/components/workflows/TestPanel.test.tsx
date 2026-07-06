import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { WorkflowTestResult } from "@/lib/api/workflows";

import { TestPanel } from "./TestPanel";

afterEach(cleanup);

const matched: WorkflowTestResult = {
  conditions_matched: true,
  error: null,
  condition_trace: [{ node_id: "cond1", result: true }],
  steps: [{ node_id: "act1", action_type: "send_email", simulated_output: { to: "a@b.c" } }],
};

const notMatched: WorkflowTestResult = {
  conditions_matched: false,
  error: null,
  condition_trace: [{ node_id: "cond1", result: false }],
  steps: [],
};

const failed: WorkflowTestResult = {
  conditions_matched: false,
  error: "action send_email failed: SMTP down",
  condition_trace: [],
  steps: [],
};

describe("TestPanel result rendering", () => {
  it("shows a matched result and the actions that would run", () => {
    render(<TestPanel running={false} result={matched} onRun={vi.fn()} />);
    expect(screen.queryByText(/Conditions matched/)).not.toBeNull();
    expect(screen.queryByText("send_email")).not.toBeNull();
  });

  it("shows 'did not match' when conditions_matched is false", () => {
    render(<TestPanel running={false} result={notMatched} onRun={vi.fn()} />);
    expect(screen.queryByText("Conditions did not match")).not.toBeNull();
  });

  it("surfaces a dry-run error over the conditions summary", () => {
    render(<TestPanel running={false} result={failed} onRun={vi.fn()} />);
    expect(screen.queryByText(/SMTP down/)).not.toBeNull();
    expect(screen.queryByText(/Conditions matched/)).toBeNull();
  });

  it("submits the operation with empty records collapsed to null", () => {
    const onRun = vi.fn();
    render(<TestPanel running={false} result={null} onRun={onRun} />);
    fireEvent.click(screen.getByRole("button", { name: "Run test" }));
    // Default op is "update"; empty `before` collapses to null, `after` keeps its seed.
    expect(onRun).toHaveBeenCalledWith({
      operation: "update",
      before: null,
      after: { status: "closed" },
    });
  });

  it("disables the run button while a dry-run is in flight", () => {
    render(<TestPanel running={true} result={null} onRun={vi.fn()} />);
    expect((screen.getByRole("button", { name: /Running/ }) as HTMLButtonElement).disabled).toBe(true);
  });
});
