/**
 * Tool-authoring helpers: a typed context object, a thin registration wrapper
 * that formats results and turns errors into clear MCP messages, and a few
 * shared Zod fragments.
 */
import { z } from "zod";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import type { CallToolResult } from "@modelcontextprotocol/sdk/types.js";
import type { ApiClient } from "../apiClient.js";
import type { BrowserSession } from "../browserSession.js";
import type { Config } from "../config.js";
import { ApiError, NoOrgError, NotAuthenticatedError } from "../errors.js";
import { logger } from "../logger.js";

export interface AppContext {
  cfg: Config;
  session: BrowserSession;
  api: ApiClient;
}

type ZodShape = z.ZodRawShape;
type ToolResult = CallToolResult;

function render(value: unknown): string {
  if (value === null || value === undefined) return "OK (no content)";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function toErrorResult(err: unknown): ToolResult {
  let text: string;
  if (err instanceof NotAuthenticatedError || err instanceof NoOrgError) {
    text = err.message;
  } else if (err instanceof ApiError) {
    const detail = typeof err.detail === "string" ? err.detail : JSON.stringify(err.detail);
    text = `Request failed (HTTP ${err.status}) on ${err.method} ${err.path}: ${detail}`;
  } else if (err instanceof Error) {
    text = `${err.name}: ${err.message}`;
  } else {
    text = `Unexpected error: ${String(err)}`;
  }
  logger.warn(`Tool error: ${text}`);
  return { content: [{ type: "text", text }], isError: true };
}

/**
 * Register a tool. The handler returns any JSON-serializable value; we format
 * it and centralize error handling so individual tools stay tiny.
 */
export function defineTool<Shape extends ZodShape>(
  server: McpServer,
  spec: {
    name: string;
    title: string;
    description: string;
    inputSchema?: Shape;
    handler: (args: z.infer<z.ZodObject<Shape>>) => Promise<unknown> | unknown;
  },
): void {
  const callback = async (args: unknown): Promise<ToolResult> => {
    try {
      const value = await spec.handler(args as z.infer<z.ZodObject<Shape>>);
      return { content: [{ type: "text", text: render(value) }] };
    } catch (err) {
      return toErrorResult(err);
    }
  };

  // The handler is fully typed via `spec.handler`; only this thin glue callback
  // is cast, because TS can't prove assignability against the still-generic Shape.
  server.registerTool(
    spec.name,
    {
      title: spec.title,
      description: spec.description,
      inputSchema: (spec.inputSchema ?? {}) as Shape,
    },
    callback as never,
  );
}

/** Shared Zod fragments. */
export const uuid = z.string().uuid();
export const paginationShape = {
  page: z.number().int().min(1).default(1).describe("1-based page number"),
  page_size: z.number().int().min(1).max(200).default(20).describe("items per page (max 200)"),
};

/** Drop keys whose value is undefined so PATCH bodies only carry set fields. */
export function pruneUndefined<T extends Record<string, unknown>>(obj: T): Partial<T> {
  const out: Partial<T> = {};
  for (const [k, v] of Object.entries(obj)) {
    if (v !== undefined) (out as Record<string, unknown>)[k] = v;
  }
  return out;
}
