/**
 * View tools. Routes under /api/views. Reads (list/get/render) are org-member;
 * create/update/delete require org-admin. Views share forms' element-tree
 * `config` and may be unbound (no entity).
 */
import { z } from "zod";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { type AppContext, defineTool, pruneUndefined, uuid } from "./util.js";

const formConfig = z.record(z.any()).describe("Element-tree config {version, elements} (passed through as JSON)");

export function registerViewTools(server: McpServer, ctx: AppContext): void {
  defineTool(server, {
    name: "km2_list_views",
    title: "List views",
    description: "List all views in the active org.",
    handler: () => ctx.api.get("/views/"),
  });

  defineTool(server, {
    name: "km2_get_view",
    title: "Get view",
    description: "Fetch a view by id, including its element-tree config.",
    inputSchema: { view_id: uuid },
    handler: ({ view_id }) => ctx.api.get(`/views/${view_id}`),
  });

  defineTool(server, {
    name: "km2_create_view",
    title: "Create view",
    description: "Create a view (org-admin). entity_definition_id is optional — views may be unbound.",
    inputSchema: {
      name: z.string().min(1).max(200),
      slug: z.string().min(1).max(63),
      description: z.string().optional(),
      entity_definition_id: uuid.optional(),
      config: formConfig.optional(),
    },
    handler: (args) => ctx.api.post("/views/", { body: pruneUndefined(args) }),
  });

  defineTool(server, {
    name: "km2_update_view",
    title: "Update view",
    description: "Update a view's name/description/config or is_active flag (org-admin). Only provided fields change.",
    inputSchema: {
      view_id: uuid,
      name: z.string().min(1).max(200).optional(),
      description: z.string().optional(),
      config: formConfig.optional(),
      is_active: z.boolean().optional(),
    },
    handler: ({ view_id, ...rest }) => ctx.api.patch(`/views/${view_id}`, { body: pruneUndefined(rest) }),
  });

  defineTool(server, {
    name: "km2_delete_view",
    title: "Delete view",
    description: "Delete a view (org-admin).",
    inputSchema: { view_id: uuid },
    handler: async ({ view_id }) => {
      await ctx.api.delete(`/views/${view_id}`);
      return { deleted: view_id };
    },
  });

  defineTool(server, {
    name: "km2_render_view",
    title: "Render view",
    description: "Get the resolved render payload for a view. Pass record_id to resolve against a specific record.",
    inputSchema: { view_id: uuid, record_id: uuid.optional() },
    handler: ({ view_id, record_id }) => ctx.api.get(`/views/${view_id}/render`, { query: { record_id } }),
  });
}
