/** Aggregate registration of every KM2 tool module. */
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import type { AppContext } from "./util.js";
import { registerSessionTools } from "./session.js";
import { registerConnectionTools } from "./connections.js";
import { registerWorkflowTools } from "./workflows.js";
import { registerDocumentTools } from "./documents.js";
import { registerFolderTools } from "./folders.js";
import { registerFormTools } from "./forms.js";
import { registerViewTools } from "./views.js";
import { registerReportTools } from "./reports.js";
import { registerEntityTools } from "./entities.js";
import { registerSearchTools } from "./search.js";

export function registerAllTools(server: McpServer, ctx: AppContext): void {
  registerSessionTools(server, ctx);
  registerConnectionTools(server, ctx);
  registerWorkflowTools(server, ctx);
  registerDocumentTools(server, ctx);
  registerFolderTools(server, ctx);
  registerFormTools(server, ctx);
  registerViewTools(server, ctx);
  registerReportTools(server, ctx);
  registerEntityTools(server, ctx);
  registerSearchTools(server, ctx);
}
