/**
 * Search tool. POST /api/search/ — semantic passage search across documents the
 * signed-in user can access (org member). Returns {hits, total}.
 */
import { z } from "zod";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { type AppContext, defineTool, pruneUndefined, uuid } from "./util.js";

export function registerSearchTools(server: McpServer, ctx: AppContext): void {
  defineTool(server, {
    name: "km2_search",
    title: "Search the knowledge base",
    description:
      "Semantic passage search over accessible documents. Returns hits with snippet text, document title, and " +
      "chunk order. Optionally scope by tags and/or folder ids.",
    inputSchema: {
      query: z.string().min(1).max(5000),
      limit: z.number().int().min(1).max(50).default(5).optional(),
      tags: z.array(z.string()).optional(),
      folder_ids: z.array(uuid).optional().describe("Restrict to these folders (ORed)"),
    },
    handler: (args) => ctx.api.post("/search/", { body: pruneUndefined(args) }),
  });
}
