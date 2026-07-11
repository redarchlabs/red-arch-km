/**
 * Report tools. Routes under /api/reports. Reads (list/get) and the run
 * endpoints are org-member; create/update/delete require org-admin.
 *
 * A report couples an aggregate `query` (a GROUP BY / metric spec over one
 * entity) with a `viz` (how to draw it). Both are passed through as JSON so the
 * tool stays in lock-step with the backend schema without duplicating it here.
 *
 * `query` shape (api.schemas.aggregate.AggregateQuery):
 *   {
 *     group_by: [{ field: "<slug>", bucket?: "day|week|month|quarter|year", alias?: "<a-z0-9_>" }],
 *     metrics:  [{ op: "count|count_distinct|sum|avg|min|max", field?: "<slug>", alias?: "<a-z0-9_>" }],
 *     filters:  [{ field: "<slug>", op: "eq|ne|gt|gte|lt|lte|in|contains|isnull", value?: any }],
 *     having:   [{ metric: "<alias>", op: "eq|ne|gt|gte|lt|lte", value: <number> }],
 *     order_by: [{ key: "<group-or-metric-alias>", dir: "asc|desc" }],
 *     limit: <1..1000>
 *   }
 * A query with no metrics defaults to a per-group row count aliased `count`.
 *
 * `viz` shape (api.schemas.report.Visualization):
 *   {
 *     type: "bar|stacked_bar|grouped_bar|line|area|stacked_area|pie|donut|scatter|table|metric",
 *     x?: "<group alias>", series?: ["<metric alias>", ...], color_by?: "<group alias>",
 *     stacked?: bool, compare_to?: "<metric alias>", unit?: "<str>",
 *     number_format?: "plain|comma|currency|percent|compact|bytes", options?: {}
 *   }
 */
import { z } from "zod";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { type AppContext, defineTool, pruneUndefined, uuid } from "./util.js";

const aggregateQuery = z
  .record(z.any())
  .describe("AggregateQuery JSON: {group_by, metrics, filters, having, order_by, limit} — see module docs");
const visualization = z
  .record(z.any())
  .describe("Visualization JSON: {type, x, series, color_by, stacked, number_format, ...} — see module docs");

export function registerReportTools(server: McpServer, ctx: AppContext): void {
  defineTool(server, {
    name: "km2_list_reports",
    title: "List reports",
    description: "List all saved reports in the active org.",
    handler: () => ctx.api.get("/reports/"),
  });

  defineTool(server, {
    name: "km2_get_report",
    title: "Get report",
    description: "Fetch a saved report by id, including its aggregate query and visualization spec.",
    inputSchema: { report_id: uuid },
    handler: ({ report_id }) => ctx.api.get(`/reports/${report_id}`),
  });

  defineTool(server, {
    name: "km2_create_report",
    title: "Create report",
    description:
      "Create a saved report (org-admin). Couples an aggregate `query` over one entity with a `viz` spec. " +
      "See the module docs for the query/viz JSON shapes.",
    inputSchema: {
      name: z.string().min(1).max(200),
      slug: z.string().min(1).max(63).describe("URL-safe slug, ^[a-z][a-z0-9_]*$"),
      entity_definition_id: uuid.describe("Entity the report aggregates over"),
      query: aggregateQuery,
      viz: visualization.optional(),
      description: z.string().optional(),
      is_active: z.boolean().optional(),
    },
    handler: (args) => ctx.api.post("/reports/", { body: pruneUndefined(args) }),
  });

  defineTool(server, {
    name: "km2_update_report",
    title: "Update report",
    description: "Update a report's name/description/query/viz or is_active flag (org-admin). Only provided fields change.",
    inputSchema: {
      report_id: uuid,
      name: z.string().min(1).max(200).optional(),
      description: z.string().optional(),
      query: aggregateQuery.optional(),
      viz: visualization.optional(),
      is_active: z.boolean().optional(),
    },
    handler: ({ report_id, ...rest }) => ctx.api.patch(`/reports/${report_id}`, { body: pruneUndefined(rest) }),
  });

  defineTool(server, {
    name: "km2_delete_report",
    title: "Delete report",
    description: "Delete a saved report (org-admin).",
    inputSchema: { report_id: uuid },
    handler: async ({ report_id }) => {
      await ctx.api.delete(`/reports/${report_id}`);
      return { deleted: report_id };
    },
  });

  defineTool(server, {
    name: "km2_run_report",
    title: "Run report",
    description:
      "Run a saved report and return the aggregate result rows. Optional `extra_filters` (ANDed onto the " +
      "report's filters) and `limit` support dashboard-style overrides.",
    inputSchema: {
      report_id: uuid,
      extra_filters: z.array(z.record(z.any())).max(20).optional().describe("FilterSpec JSON list, ANDed onto the report filters"),
      limit: z.number().int().min(1).max(1000).optional(),
    },
    handler: ({ report_id, ...rest }) => {
      const body = pruneUndefined(rest);
      return ctx.api.post(`/reports/${report_id}/run`, {
        body: Object.keys(body).length ? body : undefined,
      });
    },
  });

  defineTool(server, {
    name: "km2_run_aggregate",
    title: "Run ad-hoc aggregate",
    description:
      "Run an aggregation without saving a report (the report builder's live preview). Returns aggregate " +
      "result rows for the given entity + query.",
    inputSchema: {
      entity_definition_id: uuid,
      query: aggregateQuery,
    },
    handler: (args) => ctx.api.post("/reports/run", { body: args }),
  });
}
