/**
 * Connection tools — reusable API credentials the "Call a connected API" /
 * send_webhook workflow tasks authenticate through.
 * Routes: /api/workflows/connections (all require org-admin).
 */
import { z } from "zod";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { type AppContext, defineTool, pruneUndefined, uuid } from "./util.js";

const authType = z.enum(["none", "bearer", "api_key", "basic"]);

export function registerConnectionTools(server: McpServer, ctx: AppContext): void {
  defineTool(server, {
    name: "km2_list_connections",
    title: "List connections",
    description: "List all connected-API credentials in the active org (secrets are never returned).",
    handler: () => ctx.api.get("/workflows/connections"),
  });

  defineTool(server, {
    name: "km2_create_connection",
    title: "Create connection",
    description:
      "Create a reusable API connection. `secret` is write-only (encrypted at rest, never returned). " +
      "auth_type: none | bearer | api_key | basic.",
    inputSchema: {
      name: z.string().min(1).max(120).describe("Display name, e.g. 'robot'"),
      base_url: z.string().max(500).optional().describe("Base URL, e.g. http://localhost:8080"),
      auth_type: authType.default("none"),
      kind: z.string().max(32).default("http").describe("Connection kind (default 'http')"),
      secret: z
        .string()
        .max(4096)
        .optional()
        .describe("Credential value (bearer token / api key / basic 'user:pass'). Write-only."),
      config: z.record(z.any()).optional().describe("Arbitrary extra config (e.g. header name for api_key)"),
    },
    handler: (args) => ctx.api.post("/workflows/connections", { body: pruneUndefined(args) }),
  });

  defineTool(server, {
    name: "km2_update_connection",
    title: "Update connection",
    description:
      "Update a connection. Only provided fields change. Omit `secret` to keep the existing one; " +
      "pass a new `secret` to rotate it. (Note: `kind` cannot be changed.)",
    inputSchema: {
      connection_id: uuid,
      name: z.string().min(1).max(120).optional(),
      base_url: z.string().max(500).optional(),
      auth_type: authType.optional(),
      secret: z.string().max(4096).optional().describe("New credential value; omit to keep the current one."),
      config: z.record(z.any()).optional(),
    },
    handler: ({ connection_id, ...rest }) =>
      ctx.api.patch(`/workflows/connections/${connection_id}`, { body: pruneUndefined(rest) }),
  });

  defineTool(server, {
    name: "km2_delete_connection",
    title: "Delete connection",
    description: "Delete a connection by id. Workflows referencing it will fail to authenticate.",
    inputSchema: { connection_id: uuid },
    handler: async ({ connection_id }) => {
      await ctx.api.delete(`/workflows/connections/${connection_id}`);
      return { deleted: connection_id };
    },
  });
}
