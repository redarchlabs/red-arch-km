/**
 * Folder tools. Routes under /api/folders. list/get are org-member;
 * create/update/delete require org-admin. Folder names must not contain '.'
 * (dots are reserved for the materialized dot_path).
 */
import { z } from "zod";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { type AppContext, defineTool, paginationShape, pruneUndefined, uuid } from "./util.js";

const folderName = z
  .string()
  .min(1)
  .max(255)
  .refine((v) => !v.includes("."), { message: "Folder name must not contain '.'" });
const permConfig = z.array(z.record(z.any())).describe("List of permission-rule objects (opaque)");

export function registerFolderTools(server: McpServer, ctx: AppContext): void {
  defineTool(server, {
    name: "km2_list_folders",
    title: "List folders",
    description: "List folders (paginated). Non-admins see only folders their permission masks allow.",
    inputSchema: { ...paginationShape },
    handler: ({ page, page_size }) => ctx.api.get("/folders/", { query: { page, page_size } }),
  });

  defineTool(server, {
    name: "km2_get_folder",
    title: "Get folder",
    description: "Fetch a folder by id (includes dot_path and permission configs).",
    inputSchema: { folder_id: uuid },
    handler: ({ folder_id }) => ctx.api.get(`/folders/${folder_id}`),
  });

  defineTool(server, {
    name: "km2_create_folder",
    title: "Create folder",
    description: "Create a folder (org-admin). Optionally nest under parent_id and set viewer/contributor permissions.",
    inputSchema: {
      name: folderName,
      description: z.string().optional(),
      parent_id: uuid.optional().describe("Parent folder id; omit for a root folder"),
      viewer_permissions_config: permConfig.optional(),
      contributor_permissions_config: permConfig.optional(),
    },
    handler: (args) => ctx.api.post("/folders/", { body: pruneUndefined(args) }),
  });

  defineTool(server, {
    name: "km2_update_folder",
    title: "Update folder",
    description:
      "Update a folder (org-admin). parent_id null moves it to root; omit to leave unchanged. Changing a folder's " +
      "viewer permissions propagates to documents that inherit (haven't overridden) them.",
    inputSchema: {
      folder_id: uuid,
      name: folderName.optional(),
      description: z.string().optional(),
      parent_id: uuid.nullable().optional(),
      viewer_permissions_config: permConfig.optional(),
      contributor_permissions_config: permConfig.optional(),
    },
    handler: ({ folder_id, ...rest }) => ctx.api.patch(`/folders/${folder_id}`, { body: pruneUndefined(rest) }),
  });

  defineTool(server, {
    name: "km2_delete_folder",
    title: "Delete folder",
    description: "Delete an empty folder (org-admin). Fails with 409 if it still has child folders.",
    inputSchema: { folder_id: uuid },
    handler: async ({ folder_id }) => {
      await ctx.api.delete(`/folders/${folder_id}`);
      return { deleted: folder_id };
    },
  });
}
