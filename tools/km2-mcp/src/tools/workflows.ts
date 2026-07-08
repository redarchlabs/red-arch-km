/**
 * Workflow tools: full lifecycle (CRUD → versions → publish → test → run),
 * run monitoring, task completion, and inbound webhook endpoints.
 * Routes under /api/workflows. Most require org-admin; run + complete-task
 * require org membership (subject to the workflow's run_permission).
 */
import { z } from "zod";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { type AppContext, defineTool, pruneUndefined, uuid } from "./util.js";

const operation = z.enum(["create", "update", "delete"]);
const runPermission = z
  .object({
    mode: z.enum(["org_admin", "any_member", "roles"]).default("org_admin"),
    role_ids: z.array(uuid).default([]),
    group_ids: z.array(uuid).default([]),
  })
  .describe("Who may run this workflow");
const jsonObject = z.record(z.any());

export function registerWorkflowTools(server: McpServer, ctx: AppContext): void {
  // ---- Workflows CRUD --------------------------------------------------- //
  defineTool(server, {
    name: "km2_list_workflows",
    title: "List workflows",
    description: "List all workflows in the active org.",
    handler: () => ctx.api.get("/workflows/"),
  });

  defineTool(server, {
    name: "km2_get_workflow",
    title: "Get workflow",
    description: "Fetch a single workflow by id (includes active_version_id and run_permission).",
    inputSchema: { workflow_id: uuid },
    handler: ({ workflow_id }) => ctx.api.get(`/workflows/${workflow_id}`),
  });

  defineTool(server, {
    name: "km2_create_workflow",
    title: "Create workflow",
    description:
      "Create a workflow. Omit entity_definition_id for a manual/on-demand workflow; provide it to bind the " +
      "workflow's record-change trigger to an entity.",
    inputSchema: {
      name: z.string().min(1).max(200),
      entity_definition_id: uuid.optional().describe("Bind to an entity's create/update/delete trigger"),
      description: z.string().max(2000).optional(),
    },
    handler: (args) => ctx.api.post("/workflows/", { body: pruneUndefined(args) }),
  });

  defineTool(server, {
    name: "km2_update_workflow",
    title: "Update workflow",
    description: "Update a workflow's name/description/enabled flag or run_permission. Only provided fields change.",
    inputSchema: {
      workflow_id: uuid,
      name: z.string().min(1).max(200).optional(),
      description: z.string().max(2000).optional(),
      enabled: z.boolean().optional(),
      run_permission: runPermission.optional(),
    },
    handler: ({ workflow_id, ...rest }) =>
      ctx.api.patch(`/workflows/${workflow_id}`, { body: pruneUndefined(rest) }),
  });

  defineTool(server, {
    name: "km2_delete_workflow",
    title: "Delete workflow",
    description: "Delete a workflow and all its versions/runs.",
    inputSchema: { workflow_id: uuid },
    handler: async ({ workflow_id }) => {
      await ctx.api.delete(`/workflows/${workflow_id}`);
      return { deleted: workflow_id };
    },
  });

  // ---- Versions --------------------------------------------------------- //
  defineTool(server, {
    name: "km2_list_workflow_versions",
    title: "List workflow versions",
    description: "List all draft/published versions of a workflow.",
    inputSchema: { workflow_id: uuid },
    handler: ({ workflow_id }) => ctx.api.get(`/workflows/${workflow_id}/versions`),
  });

  defineTool(server, {
    name: "km2_save_workflow_definition",
    title: "Save workflow version (draft)",
    description:
      "Save a new draft version from a BPMN-style graph `definition` (nodes + edges JSON). This does NOT " +
      "publish it — use km2_publish_workflow to make a version live.",
    inputSchema: {
      workflow_id: uuid,
      definition: jsonObject.describe("Workflow graph object (nodes, edges, node configs)"),
    },
    handler: ({ workflow_id, definition }) =>
      ctx.api.post(`/workflows/${workflow_id}/versions`, { body: { definition } }),
  });

  defineTool(server, {
    name: "km2_publish_workflow",
    title: "Publish workflow version",
    description: "Publish a specific version, making it the active version that real triggers/runs use.",
    inputSchema: { workflow_id: uuid, version_id: uuid },
    handler: ({ workflow_id, version_id }) =>
      ctx.api.post(`/workflows/${workflow_id}/versions/${version_id}/publish`),
  });

  defineTool(server, {
    name: "km2_test_workflow",
    title: "Test workflow version (dry run)",
    description:
      "Simulate a record change against a saved version without writing any data. Returns which conditions " +
      "matched and the simulated steps.",
    inputSchema: {
      workflow_id: uuid,
      version_id: uuid,
      operation: operation.default("update"),
      before: jsonObject.optional().describe("Record state before the change (for update/delete)"),
      after: jsonObject.optional().describe("Record state after the change (for create/update)"),
    },
    handler: ({ workflow_id, version_id, ...rest }) =>
      ctx.api.post(`/workflows/${workflow_id}/versions/${version_id}/test`, { body: pruneUndefined(rest) }),
  });

  // ---- Run + monitor ---------------------------------------------------- //
  defineTool(server, {
    name: "km2_run_workflow",
    title: "Run workflow",
    description:
      "Execute the PUBLISHED version for real. Provide `inputs` for a manual/on-demand workflow, or " +
      "before/after (+ record_id) to simulate a record trigger. Subject to the workflow's run_permission.",
    inputSchema: {
      workflow_id: uuid,
      operation: operation.default("update"),
      record_id: uuid.optional(),
      before: jsonObject.optional(),
      after: jsonObject.optional(),
      inputs: jsonObject.optional().describe("Manual-trigger input variables"),
    },
    handler: ({ workflow_id, ...rest }) =>
      ctx.api.post(`/workflows/${workflow_id}/run`, { body: pruneUndefined(rest), requireOrg: true }),
  });

  defineTool(server, {
    name: "km2_recent_runs",
    title: "Recent workflow runs",
    description: "List recent runs across all workflows in the org (most recent first).",
    inputSchema: { limit: z.number().int().min(1).max(100).default(25).optional() },
    handler: ({ limit }) => ctx.api.get("/workflows/runs/recent", { query: { limit } }),
  });

  defineTool(server, {
    name: "km2_list_workflow_runs",
    title: "List runs for a workflow",
    description: "List runs for a single workflow (most recent first).",
    inputSchema: { workflow_id: uuid, limit: z.number().int().min(1).max(200).default(50).optional() },
    handler: ({ workflow_id, limit }) => ctx.api.get(`/workflows/${workflow_id}/runs`, { query: { limit } }),
  });

  defineTool(server, {
    name: "km2_get_run_steps",
    title: "Get run steps",
    description: "List the executed steps of a run (status, attempts, output, error per node).",
    inputSchema: { run_id: uuid },
    handler: ({ run_id }) => ctx.api.get(`/workflows/runs/${run_id}/steps`),
  });

  defineTool(server, {
    name: "km2_complete_task",
    title: "Complete a human/user task",
    description:
      "Complete a waiting user-task step in a run, supplying its output/variables so the run can continue.",
    inputSchema: {
      run_id: uuid,
      node_id: z.string().optional().describe("The waiting task node id (optional if unambiguous)"),
      variables: jsonObject.optional(),
      output: jsonObject.optional(),
    },
    handler: ({ run_id, ...rest }) =>
      ctx.api.post(`/workflows/runs/${run_id}/complete-task`, { body: pruneUndefined(rest), requireOrg: true }),
  });

  // ---- Inbound webhook endpoints --------------------------------------- //
  defineTool(server, {
    name: "km2_list_inbound_endpoints",
    title: "List inbound webhook endpoints",
    description: "List inbound webhook endpoints that trigger workflows.",
    handler: () => ctx.api.get("/workflows/inbound-endpoints"),
  });

  defineTool(server, {
    name: "km2_create_inbound_endpoint",
    title: "Create inbound webhook endpoint",
    description:
      "Create an inbound webhook endpoint bound to a workflow. The response includes the URL, token, and " +
      "signing secret — shown ONCE, so capture them.",
    inputSchema: { name: z.string().min(1).max(120), workflow_id: uuid },
    handler: (args) => ctx.api.post("/workflows/inbound-endpoints", { body: args }),
  });

  defineTool(server, {
    name: "km2_delete_inbound_endpoint",
    title: "Delete inbound webhook endpoint",
    description: "Delete an inbound webhook endpoint by id.",
    inputSchema: { endpoint_id: uuid },
    handler: async ({ endpoint_id }) => {
      await ctx.api.delete(`/workflows/inbound-endpoints/${endpoint_id}`);
      return { deleted: endpoint_id };
    },
  });
}
