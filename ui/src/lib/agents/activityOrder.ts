import type { Agent, AgentActivity } from "@/lib/api/agents";

/**
 * Roster order: the agents doing something rise to the top.
 *
 * An alphabetical roster buries the one agent that is stuck behind fourteen that are
 * asleep, and the whole point of the badges is to be seen without hunting. Three bands,
 * worst-first — the same worst-first rule the work-order lanes use:
 *
 * 1. **needs you** — a person is the blocker, so this is the only band with something
 *    for you to do.
 * 2. **working** — informational; it is moving on its own.
 * 3. everything else, in the order the server sent (name), so the resting state of the
 *    page is still a predictable alphabetical list.
 *
 * The sort is stable, so agents inside a band keep their existing order rather than
 * shuffling on every poll — a list that reorders under the cursor is worse than one
 * that is merely sorted wrong.
 */
const BAND: Record<AgentActivity["state"], number> = {
  needs_you: 0,
  working: 1,
};
const IDLE = 2;

export function sortByActivity(
  agents: readonly Agent[],
  activity: Readonly<Record<string, AgentActivity | undefined>>,
): Agent[] {
  const bandOf = (agent: Agent): number => {
    const state = activity[agent.id]?.state;
    return state === undefined ? IDLE : BAND[state];
  };
  return [...agents].sort((a, b) => bandOf(a) - bandOf(b));
}
