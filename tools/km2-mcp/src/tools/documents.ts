/**
 * Document tools. Routes under /api/documents (org members; unfiled docs are
 * org-admin-only on list). Note: binary/file upload (multipart) is intentionally
 * out of scope for the MCP — use km2_create_document with `text` for text docs.
 */
import { z } from "zod";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { type AppContext, defineTool, paginationShape, pruneUndefined, uuid } from "./util.js";

const permConfig = z
  .array(z.record(z.any()))
  .describe("List of permission-rule objects (opaque; recomputes masks when set)");

export function registerDocumentTools(server: McpServer, ctx: AppContext): void {
  defineTool(server, {
    name: "km2_list_documents",
    title: "List documents",
    description: "List documents (paginated). Optionally filter by folder. Unfiled docs are visible to admins only.",
    inputSchema: {
      ...paginationShape,
      folder_id: uuid.optional().describe("Filter to a single folder"),
    },
    handler: ({ page, page_size, folder_id }) =>
      ctx.api.get("/documents/", { query: { page, page_size, folder_id } }),
  });

  defineTool(server, {
    name: "km2_get_document",
    title: "Get document",
    description: "Fetch a document's metadata by id.",
    inputSchema: { document_id: uuid },
    handler: ({ document_id }) => ctx.api.get(`/documents/${document_id}`),
  });

  defineTool(server, {
    name: "km2_create_document",
    title: "Create document",
    description:
      "Create a text document. New docs inherit their folder's permissions; set per-doc permissions later via " +
      "km2_update_document. For binary/PDF uploads, use the web UI (multipart upload is not exposed here).",
    inputSchema: {
      title: z.string().min(1).max(255),
      description: z.string().optional(),
      text: z.string().optional().describe("Document body (markdown/plain text)"),
      folder_id: uuid.optional(),
      tag_ids: z.array(uuid).optional(),
      metadata: z.record(z.any()).optional(),
      use_knowledge_graph: z.boolean().optional(),
    },
    handler: (args) => ctx.api.post("/documents/", { body: pruneUndefined(args) }),
  });

  defineTool(server, {
    name: "km2_update_document",
    title: "Update document",
    description:
      "Update document metadata and/or permissions. Only provided fields change. Setting a folder_id of null " +
      "moves it to unfiled; setting viewer/contributor permission configs recomputes its access masks.",
    inputSchema: {
      document_id: uuid,
      title: z.string().min(1).max(255).optional(),
      description: z.string().optional(),
      folder_id: uuid.nullable().optional(),
      tag_ids: z.array(uuid).optional(),
      metadata: z.record(z.any()).optional(),
      viewer_permissions_config: permConfig.optional(),
      contributor_permissions_config: permConfig.optional(),
    },
    handler: ({ document_id, ...rest }) =>
      ctx.api.patch(`/documents/${document_id}`, { body: pruneUndefined(rest) }),
  });

  defineTool(server, {
    name: "km2_delete_document",
    title: "Delete document",
    description: "Delete a document (also purges its vectors on re-index).",
    inputSchema: { document_id: uuid },
    handler: async ({ document_id }) => {
      await ctx.api.delete(`/documents/${document_id}`);
      return { deleted: document_id };
    },
  });

  defineTool(server, {
    name: "km2_get_document_content",
    title: "Get document content",
    description: "Fetch a document's rendered content ({content, format, kind, original_url}).",
    inputSchema: { document_id: uuid },
    handler: ({ document_id }) => ctx.api.get(`/documents/${document_id}/content`),
  });

  defineTool(server, {
    name: "km2_update_document_content",
    title: "Update document content",
    description:
      "Replace an editable text document's body and re-ingest it. Only works for text/markdown originals " +
      "(415 otherwise). An empty string clears the body and skips re-ingest.",
    inputSchema: { document_id: uuid, text: z.string().describe("New full document body") },
    handler: ({ document_id, text }) =>
      ctx.api.request("PUT", `/documents/${document_id}/content`, { body: { text } }),
  });

  defineTool(server, {
    name: "km2_reprocess_document",
    title: "Reprocess document",
    description: "Re-run ingestion for a FAILED or stale document (uploader or org-admin only).",
    inputSchema: { document_id: uuid },
    handler: ({ document_id }) => ctx.api.post(`/documents/${document_id}/reprocess`),
  });
}
