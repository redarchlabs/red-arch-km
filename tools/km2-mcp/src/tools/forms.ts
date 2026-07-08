/**
 * Form tools. Routes under /api/forms. Admin CRUD + links require org-admin;
 * render/submit require org membership. `config` is the v2 element-tree
 * ({version, elements[]}); it's passed through as opaque JSON.
 */
import { z } from "zod";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { type AppContext, defineTool, pruneUndefined, uuid } from "./util.js";

const slug = z
  .string()
  .min(1)
  .max(63)
  .describe("URL-safe slug");
const formConfig = z
  .record(z.any())
  .describe("Element-tree config: {version:int, elements:[...]} (recursive; passed through as JSON)");

export function registerFormTools(server: McpServer, ctx: AppContext): void {
  defineTool(server, {
    name: "km2_list_forms",
    title: "List forms",
    description: "List all forms in the active org (org-admin).",
    handler: () => ctx.api.get("/forms/"),
  });

  defineTool(server, {
    name: "km2_get_form",
    title: "Get form",
    description: "Fetch a form by id, including its element-tree config.",
    inputSchema: { form_id: uuid },
    handler: ({ form_id }) => ctx.api.get(`/forms/${form_id}`),
  });

  defineTool(server, {
    name: "km2_create_form",
    title: "Create form",
    description: "Create a form bound to an entity definition (org-admin).",
    inputSchema: {
      name: z.string().min(1).max(200),
      slug,
      entity_definition_id: uuid.describe("Entity the form writes to"),
      description: z.string().optional(),
      config: formConfig.optional(),
    },
    handler: (args) => ctx.api.post("/forms/", { body: pruneUndefined(args) }),
  });

  defineTool(server, {
    name: "km2_update_form",
    title: "Update form",
    description: "Update a form's name/description/config or is_active flag (org-admin). Only provided fields change.",
    inputSchema: {
      form_id: uuid,
      name: z.string().min(1).max(200).optional(),
      description: z.string().optional(),
      config: formConfig.optional(),
      is_active: z.boolean().optional(),
    },
    handler: ({ form_id, ...rest }) => ctx.api.patch(`/forms/${form_id}`, { body: pruneUndefined(rest) }),
  });

  defineTool(server, {
    name: "km2_delete_form",
    title: "Delete form",
    description: "Delete a form (org-admin).",
    inputSchema: { form_id: uuid },
    handler: async ({ form_id }) => {
      await ctx.api.delete(`/forms/${form_id}`);
      return { deleted: form_id };
    },
  });

  defineTool(server, {
    name: "km2_list_form_links",
    title: "List form links",
    description: "List shareable links generated for a form (org-admin).",
    inputSchema: { form_id: uuid },
    handler: ({ form_id }) => ctx.api.get(`/forms/${form_id}/links`),
  });

  defineTool(server, {
    name: "km2_create_form_link",
    title: "Create form link",
    description:
      "Generate a shareable (optionally emailed) link that opens the form for a specific record. The response " +
      "includes the token/url shown once.",
    inputSchema: {
      form_id: uuid,
      target_record_id: uuid.describe("Record the link edits"),
      recipient_email: z.string().email().optional(),
      expires_in_days: z.number().int().min(1).max(365).optional().describe("Default 14"),
    },
    handler: ({ form_id, ...rest }) => ctx.api.post(`/forms/${form_id}/links`, { body: pruneUndefined(rest) }),
  });

  defineTool(server, {
    name: "km2_revoke_form_link",
    title: "Revoke form link",
    description: "Revoke a previously issued form link.",
    inputSchema: { form_id: uuid, link_id: uuid },
    handler: ({ form_id, link_id }) => ctx.api.post(`/forms/${form_id}/links/${link_id}/revoke`),
  });

  defineTool(server, {
    name: "km2_render_form",
    title: "Render form",
    description:
      "Get the resolved render payload for a form (config + entity catalog + current values). Pass record_id to " +
      "prefill from an existing record.",
    inputSchema: { form_id: uuid, record_id: uuid.optional() },
    handler: ({ form_id, record_id }) => ctx.api.get(`/forms/${form_id}/render`, { query: { record_id } }),
  });

  defineTool(server, {
    name: "km2_submit_form",
    title: "Submit form (internal)",
    description:
      "Submit a form as a signed-in member against an existing record. `related` maps relationship ids to " +
      "{values} (1:1) or {rows:[...]} (1:M).",
    inputSchema: {
      form_id: uuid,
      record_id: uuid.describe("The record being written"),
      values: z.record(z.any()).optional(),
      related: z.record(z.any()).optional(),
    },
    handler: async ({ form_id, record_id, values, related }) => {
      await ctx.api.post(`/forms/${form_id}/submit`, {
        body: pruneUndefined({ record_id, values, related }),
      });
      return { submitted: true, form_id, record_id };
    },
  });
}
