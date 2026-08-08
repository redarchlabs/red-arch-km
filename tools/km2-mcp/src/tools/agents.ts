/**
 * Agent tools — the org's agent roster: who exists, what class they are, what
 * they may do, and who they report to.
 * Routes: /api/agents (create/update/delete require org-admin).
 *
 * An agent roster is org configuration, not code — the same as entities, forms
 * and workflows — so it is built through these tools rather than seeded from a
 * file in the repo.
 */
import { z } from "zod";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { type AppContext, defineTool, pruneUndefined, uuid } from "./util.js";

/**
 * The governance class, enforced by the kind-gate *before* grants are consulted.
 * Getting this wrong mis-governs the agent no matter what grants say.
 */
const kind = z
  .enum(["coordinator", "advisory", "operator"])
  .describe(
    "coordinator = plans + delegates, may not act directly; " +
      "advisory = reads + advises, may never mutate; " +
      "operator = the only class that may take side-effecting action.",
  );

const grants = z
  .object({
    tools: z.array(z.string()).optional().describe("Tool names this agent may use, e.g. ['search_knowledge']."),
    records_write: z.boolean().optional().describe("Required (with operator kind) to mutate records."),
    approval_required: z
      .array(z.string())
      .optional()
      .describe("Tools that pause for a human approval instead of running — the 'ask' tier."),
  })
  .describe("Capability grants. The kind-gate still applies on top: it can only narrow, never widen.");

export function registerAgentTools(server: McpServer, ctx: AppContext): void {
  defineTool(server, {
    name: "km2_list_agents",
    title: "List agents",
    description:
      "List the agent roster for the active org — name, kind, supervisor, grants and enabled state. " +
      "Use this to read the org chart before wiring supervisors.",
    handler: () => ctx.api.get("/agents/"),
  });

  defineTool(server, {
    name: "km2_get_agent",
    title: "Get agent",
    description: "Fetch one agent by id, including its full persona (system prompt).",
    inputSchema: { agent_id: uuid },
    handler: ({ agent_id }) => ctx.api.get(`/agents/${agent_id}`),
  });

  defineTool(server, {
    name: "km2_create_agent",
    title: "Create agent",
    description:
      "Add an agent to the org roster. `persona` is its system prompt. `supervisor_id` is the org " +
      "chart — omit it for the apex agent, which escalates to a human instead. Delegation is only " +
      "permitted to DIRECT reports, so the chart is what decides who may hand work to whom.",
    inputSchema: {
      name: z.string().min(1).max(120).describe("Unique within the org, e.g. 'backend-engineer'."),
      display_name: z.string().max(200).optional(),
      description: z.string().optional().describe("One line on what this agent is for."),
      kind: kind.default("operator"),
      persona: z.string().optional().describe("System prompt: the agent's role, rules and routing."),
      provider: z.string().min(1).max(40).describe("LLM provider, e.g. 'anthropic' or 'openai'."),
      model: z.string().min(1).max(120).describe("Model id, e.g. 'claude-sonnet-5'."),
      params: z.record(z.any()).optional().describe("Sampling params, e.g. {temperature: 0.2}."),
      supervisor_id: uuid.optional().describe("The agent this one reports to. Omit for the apex."),
      avatar: z.string().max(16).optional(),
      accent: z.string().max(16).optional(),
      enabled: z.boolean().optional(),
      grants: grants.optional(),
      workflow_allowlist: z.array(uuid).optional().describe("Workflows this agent may run."),
      workflow_invocable: z
        .array(z.string())
        .optional()
        .describe("Consent mirror: which workflows may bind this agent to an agent_task step, or ['*']."),
    },
    handler: (args) => ctx.api.post("/agents/", { body: pruneUndefined(args) }),
  });

  defineTool(server, {
    name: "km2_update_agent",
    title: "Update agent",
    description:
      "Update an agent; only provided fields change. Use this for the second pass that wires " +
      "`supervisor_id`, since a supervisor usually has to be created after its reports.",
    inputSchema: {
      agent_id: uuid,
      display_name: z.string().max(200).optional(),
      description: z.string().optional(),
      kind: kind.optional(),
      persona: z.string().optional(),
      provider: z.string().min(1).max(40).optional(),
      model: z.string().min(1).max(120).optional(),
      params: z.record(z.any()).optional(),
      supervisor_id: uuid.nullable().optional().describe("Pass null to move the agent to the apex."),
      avatar: z.string().max(16).optional(),
      accent: z.string().max(16).optional(),
      enabled: z.boolean().optional(),
      grants: grants.optional(),
      workflow_allowlist: z.array(uuid).optional(),
      workflow_invocable: z.array(z.string()).optional(),
    },
    handler: ({ agent_id, ...rest }) =>
      ctx.api.patch(`/agents/${agent_id}`, { body: pruneUndefined(rest) }),
  });

  defineTool(server, {
    name: "km2_list_agent_schedules",
    title: "List agent schedules",
    description: "List the cron schedules for one agent, with their last/next firing times.",
    inputSchema: { agent_id: uuid },
    handler: ({ agent_id }) => ctx.api.get(`/agents/${agent_id}/schedules`),
  });

  defineTool(server, {
    name: "km2_create_agent_schedule",
    title: "Create agent schedule",
    description:
      "Give an agent a standing cron instruction. Created DISABLED unless you pass enabled: true — " +
      "configuring a roster should not start firing unattended work. Turn it on once the agent " +
      "behaves as intended when run by hand.",
    inputSchema: {
      agent_id: uuid,
      cron: z.string().min(1).max(120).describe("5-field cron, evaluated in UTC, e.g. '0 9 * * *'."),
      task: z.string().min(1).describe("The instruction handed to the agent on each firing."),
      enabled: z.boolean().optional().describe("Defaults to false."),
    },
    handler: (args) => ctx.api.post("/agents/schedules", { body: pruneUndefined(args) }),
  });

  defineTool(server, {
    name: "km2_update_agent_schedule",
    title: "Update agent schedule",
    description:
      "Update a schedule; only provided fields change. Use this to enable one after verifying the " +
      "agent by hand. Changing `cron` clears the cached next firing so the new expression takes effect.",
    inputSchema: {
      schedule_id: uuid,
      cron: z.string().min(1).max(120).optional(),
      task: z.string().min(1).optional(),
      enabled: z.boolean().optional(),
    },
    handler: ({ schedule_id, ...rest }) =>
      ctx.api.patch(`/agents/schedules/${schedule_id}`, { body: pruneUndefined(rest) }),
  });

  defineTool(server, {
    name: "km2_delete_agent_schedule",
    title: "Delete agent schedule",
    description: "Delete a schedule by id. The agent remains; it just stops firing on that cron.",
    inputSchema: { schedule_id: uuid },
    handler: async ({ schedule_id }) => {
      await ctx.api.delete(`/agents/schedules/${schedule_id}`);
      return { deleted: schedule_id };
    },
  });

  defineTool(server, {
    name: "km2_delete_agent",
    title: "Delete agent",
    description:
      "Delete an agent by id. Agents reporting to it are moved to the apex (their supervisor is " +
      "cleared), so delete a supervisor only after re-pointing its reports.",
    inputSchema: { agent_id: uuid },
    handler: async ({ agent_id }) => {
      await ctx.api.delete(`/agents/${agent_id}`);
      return { deleted: agent_id };
    },
  });
}
