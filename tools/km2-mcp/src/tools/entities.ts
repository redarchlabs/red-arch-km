/**
 * Entity tools: entity definitions + fields + relationships (org-admin, under
 * /api/entity-definitions) and entity records (org-member, under
 * /api/entities/{slug}/records). Record bodies are bare field-slug→value maps.
 */
import { z } from "zod";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { type AppContext, defineTool, paginationShape, pruneUndefined, uuid } from "./util.js";

const fieldType = z.enum([
  "text",
  "long_text",
  "integer",
  "bigint",
  "numeric",
  "boolean",
  "date",
  "timestamptz",
  "uuid",
  "json",
  "picklist",
]);

const fieldCreate = z.object({
  name: z.string().min(1).max(100),
  slug: z.string().min(1).max(63).describe("lowercase, ^[a-z][a-z0-9_]*$"),
  field_type: fieldType,
  picklist_options: z.array(z.string()).optional().describe("Required when field_type = picklist"),
  is_required: z.boolean().optional(),
  is_unique: z.boolean().optional(),
  default_value: z.any().optional(),
  order: z.number().int().min(0).optional(),
});

export function registerEntityTools(server: McpServer, ctx: AppContext): void {
  // ---- Entity definitions ---------------------------------------------- //
  defineTool(server, {
    name: "km2_list_entities",
    title: "List entity definitions",
    description: "List custom entity definitions (paginated).",
    inputSchema: { ...paginationShape },
    handler: ({ page, page_size }) => ctx.api.get("/entity-definitions/", { query: { page, page_size } }),
  });

  defineTool(server, {
    name: "km2_get_entity",
    title: "Get entity definition",
    description: "Fetch an entity definition (including its fields) by id.",
    inputSchema: { definition_id: uuid },
    handler: ({ definition_id }) => ctx.api.get(`/entity-definitions/${definition_id}`),
  });

  defineTool(server, {
    name: "km2_create_entity",
    title: "Create entity definition",
    description: "Create a custom entity definition, optionally with an initial set of fields (org-admin).",
    inputSchema: {
      name: z.string().min(1).max(100),
      slug: z.string().min(1).max(63).describe("lowercase, ^[a-z][a-z0-9_]*$"),
      description: z.string().max(2000).optional(),
      fields: z.array(fieldCreate).optional(),
    },
    handler: (args) => ctx.api.post("/entity-definitions/", { body: pruneUndefined(args) }),
  });

  defineTool(server, {
    name: "km2_update_entity",
    title: "Update entity definition",
    description: "Update an entity definition's name/description/active flag (org-admin).",
    inputSchema: {
      definition_id: uuid,
      name: z.string().min(1).max(100).optional(),
      description: z.string().max(2000).optional(),
      is_active: z.boolean().optional(),
    },
    handler: ({ definition_id, ...rest }) =>
      ctx.api.patch(`/entity-definitions/${definition_id}`, { body: pruneUndefined(rest) }),
  });

  defineTool(server, {
    name: "km2_delete_entity",
    title: "Delete entity definition",
    description: "Delete an entity definition (org-admin). Pass force=true to delete even if it has records (destructive).",
    inputSchema: { definition_id: uuid, force: z.boolean().default(false).optional() },
    handler: async ({ definition_id, force }) => {
      await ctx.api.delete(`/entity-definitions/${definition_id}`, { query: { force } });
      return { deleted: definition_id };
    },
  });

  defineTool(server, {
    name: "km2_add_entity_field",
    title: "Add entity field",
    description: "Add a field (column) to an entity definition (org-admin).",
    inputSchema: {
      definition_id: uuid,
      name: z.string().min(1).max(100),
      slug: z.string().min(1).max(63),
      field_type: fieldType,
      picklist_options: z.array(z.string()).optional(),
      is_required: z.boolean().optional(),
      is_unique: z.boolean().optional(),
      default_value: z.any().optional(),
      order: z.number().int().min(0).optional(),
    },
    handler: ({ definition_id, ...field }) =>
      ctx.api.post(`/entity-definitions/${definition_id}/fields`, { body: pruneUndefined(field) }),
  });

  defineTool(server, {
    name: "km2_delete_entity_field",
    title: "Delete entity field",
    description: "Drop a field (column) from an entity definition (org-admin). Destructive — data in the column is lost.",
    inputSchema: { definition_id: uuid, field_id: uuid },
    handler: async ({ definition_id, field_id }) => {
      await ctx.api.delete(`/entity-definitions/${definition_id}/fields/${field_id}`);
      return { deleted: field_id };
    },
  });

  defineTool(server, {
    name: "km2_list_entity_relationships",
    title: "List entity relationships",
    description: "List an entity's relationships. direction=outgoing (default) or incoming.",
    inputSchema: {
      definition_id: uuid,
      direction: z.enum(["outgoing", "incoming"]).default("outgoing").optional(),
    },
    handler: ({ definition_id, direction }) => {
      const path =
        direction === "incoming"
          ? `/entity-definitions/${definition_id}/incoming-relationships`
          : `/entity-definitions/${definition_id}/relationships`;
      return ctx.api.get(path);
    },
  });

  defineTool(server, {
    name: "km2_create_entity_relationship",
    title: "Create entity relationship",
    description: "Define a relationship from this entity to a target entity (org-admin).",
    inputSchema: {
      definition_id: uuid,
      name: z.string().min(1).max(100),
      slug: z.string().min(1).max(63),
      cardinality: z.enum(["one_to_one", "one_to_many", "many_to_one", "many_to_many"]),
      target_definition_id: uuid,
      on_delete: z.enum(["CASCADE", "SET NULL", "RESTRICT"]).default("SET NULL").optional(),
      is_required: z.boolean().optional(),
    },
    handler: ({ definition_id, ...rest }) =>
      ctx.api.post(`/entity-definitions/${definition_id}/relationships`, { body: pruneUndefined(rest) }),
  });

  // ---- Entity records --------------------------------------------------- //
  defineTool(server, {
    name: "km2_list_records",
    title: "List entity records",
    description:
      "List records of an entity (by its slug). Keyset-paginated: response is {items, next_cursor, limit}. " +
      "Pass next_cursor back in `cursor` to page forward; `q` does a substring search.",
    inputSchema: {
      slug: z.string().describe("Entity definition slug"),
      q: z.string().max(200).optional(),
      cursor: z.string().optional().describe("Opaque next_cursor from a previous page"),
      limit: z.number().int().min(1).max(200).default(50).optional(),
    },
    handler: ({ slug, q, cursor, limit }) =>
      ctx.api.get(`/entities/${encodeURIComponent(slug)}/records`, { query: { q, cursor, limit } }),
  });

  defineTool(server, {
    name: "km2_get_record",
    title: "Get entity record",
    description: "Fetch a single record by entity slug + record id.",
    inputSchema: { slug: z.string(), record_id: uuid },
    handler: ({ slug, record_id }) => ctx.api.get(`/entities/${encodeURIComponent(slug)}/records/${record_id}`),
  });

  defineTool(server, {
    name: "km2_create_record",
    title: "Create entity record",
    description:
      "Create a record. `values` is a map of field-slug → value (validated server-side against the entity catalog).",
    inputSchema: { slug: z.string(), values: z.record(z.any()).describe("field-slug → value") },
    handler: ({ slug, values }) => ctx.api.post(`/entities/${encodeURIComponent(slug)}/records`, { body: values }),
  });

  defineTool(server, {
    name: "km2_update_record",
    title: "Update entity record",
    description: "Update a record's fields. `values` is a map of field-slug → new value (partial update).",
    inputSchema: { slug: z.string(), record_id: uuid, values: z.record(z.any()) },
    handler: ({ slug, record_id, values }) =>
      ctx.api.patch(`/entities/${encodeURIComponent(slug)}/records/${record_id}`, { body: values }),
  });

  defineTool(server, {
    name: "km2_delete_record",
    title: "Delete entity record",
    description: "Delete a record by entity slug + record id.",
    inputSchema: { slug: z.string(), record_id: uuid },
    handler: async ({ slug, record_id }) => {
      await ctx.api.delete(`/entities/${encodeURIComponent(slug)}/records/${record_id}`);
      return { deleted: record_id };
    },
  });
}
