import { describe, expect, it } from "vitest";

import { sortByActivity } from "./activityOrder";
import type { Agent, AgentActivity } from "@/lib/api/agents";

const agent = (id: string): Agent =>
  ({
    id,
    name: id,
    kind: "operator",
    provider: "openai",
    model: "m",
    supervisor_id: null,
    enabled: true,
  }) as unknown as Agent;

const busy = (id: string): AgentActivity => ({
  agent_id: id,
  state: "working",
  live_runs: 1,
  waiting_on_you: 0,
});

const stuck = (id: string): AgentActivity => ({
  agent_id: id,
  state: "needs_you",
  live_runs: 0,
  waiting_on_you: 1,
});

const names = (agents: Agent[]) => agents.map((a) => a.name);

describe("sortByActivity", () => {
  it("puts the agents that need you above the ones merely working", () => {
    const roster = [agent("aa-idle"), agent("bb-busy"), agent("cc-stuck")];

    const out = sortByActivity(roster, { "bb-busy": busy("bb-busy"), "cc-stuck": stuck("cc-stuck") });

    expect(names(out)).toEqual(["cc-stuck", "bb-busy", "aa-idle"]);
  });

  it("leaves an all-idle roster in the order it arrived", () => {
    // The resting state of the page stays a predictable alphabetical list.
    const roster = [agent("alpha"), agent("beta"), agent("gamma")];

    expect(names(sortByActivity(roster, {}))).toEqual(["alpha", "beta", "gamma"]);
  });

  it("keeps agents inside a band in their original order", () => {
    // A list that reshuffles under the cursor on every poll is worse than one that
    // is merely sorted wrong, so the sort has to be stable.
    const roster = [agent("one"), agent("two"), agent("three")];

    const out = sortByActivity(roster, {
      one: busy("one"),
      two: busy("two"),
      three: busy("three"),
    });

    expect(names(out)).toEqual(["one", "two", "three"]);
  });

  it("does not mutate the roster it was given", () => {
    const roster = [agent("idle"), agent("stuck")];

    sortByActivity(roster, { stuck: stuck("stuck") });

    expect(names(roster)).toEqual(["idle", "stuck"]);
  });
});
