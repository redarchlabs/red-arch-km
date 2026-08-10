import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AgentActivityBadge } from "./AgentActivityBadge";
import type { AgentActivity } from "@/lib/api/agents";

const activity = (over: Partial<AgentActivity>): AgentActivity => ({
  agent_id: "a1",
  state: "working",
  live_runs: 1,
  waiting_on_you: 0,
  ...over,
});

describe("AgentActivityBadge", () => {
  it("says nothing for an idle agent", () => {
    const { container } = render(<AgentActivityBadge activity={undefined} />);

    // A row of "idle" badges is noise that makes the two that matter harder to find.
    expect(container.textContent).toBe("");
  });

  it("shows Working while a run is in flight", () => {
    render(<AgentActivityBadge activity={activity({ state: "working", live_runs: 1 })} />);

    expect(screen.getByText("Working")).toBeTruthy();
  });

  it("counts concurrent runs", () => {
    render(<AgentActivityBadge activity={activity({ state: "working", live_runs: 3 })} />);

    expect(screen.getByText("Working (3)")).toBeTruthy();
  });

  it("shows Needs you when a person is the blocker", () => {
    render(
      <AgentActivityBadge activity={activity({ state: "needs_you", live_runs: 0, waiting_on_you: 1 })} />,
    );

    expect(screen.getByText("Needs you")).toBeTruthy();
    expect(screen.queryByText(/Working/)).toBeNull();
  });

  it("counts more than one thing waiting", () => {
    render(
      <AgentActivityBadge activity={activity({ state: "needs_you", live_runs: 0, waiting_on_you: 2 })} />,
    );

    expect(screen.getByText("Needs you (2)")).toBeTruthy();
  });
});
