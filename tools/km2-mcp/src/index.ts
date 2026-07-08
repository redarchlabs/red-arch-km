#!/usr/bin/env node
/**
 * KM2 MCP server entry point (stdio transport).
 *
 * Wires config → BrowserSession (live Clerk session) → ApiClient → tools, then
 * serves over stdio so Claude Code (or any MCP client) can drive the KM2 API on
 * behalf of the signed-in user. The browser launches lazily on the first tool
 * call that needs auth, so `km2_status`/`km2_login` is the natural first step.
 */
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { ApiClient } from "./apiClient.js";
import { BrowserSession } from "./browserSession.js";
import { loadConfig } from "./config.js";
import { logger } from "./logger.js";
import { registerAllTools } from "./tools/index.js";
import type { AppContext } from "./tools/util.js";

async function main(): Promise<void> {
  const cfg = loadConfig();
  const session = new BrowserSession(cfg);
  const api = new ApiClient(cfg, session);
  const ctx: AppContext = { cfg, session, api };

  const server = new McpServer({ name: "km2", version: "0.1.0" });
  registerAllTools(server, ctx);

  let shuttingDown = false;
  const shutdown = async (signal: string) => {
    if (shuttingDown) return;
    shuttingDown = true;
    logger.info(`Received ${signal}, shutting down…`);
    await session.close();
    process.exit(0);
  };
  process.on("SIGINT", () => void shutdown("SIGINT"));
  process.on("SIGTERM", () => void shutdown("SIGTERM"));

  const transport = new StdioServerTransport();
  await server.connect(transport);
  logger.info(`KM2 MCP server ready (app=${cfg.appUrl}, api=${cfg.apiUrl})`);
}

main().catch((err) => {
  logger.error("Fatal error starting KM2 MCP server", err instanceof Error ? err.stack ?? err.message : err);
  process.exit(1);
});
