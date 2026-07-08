/**
 * Session / auth tools: sign in via the browser, inspect state, and pick the
 * active organization. These are the entry points — everything else needs a
 * signed-in session with an active org.
 */
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { type AppContext, defineTool, uuid } from "./util.js";

interface OrgSummary {
  id: string;
  name?: string;
  is_org_admin?: boolean;
}
interface MeResponse {
  id?: string;
  username?: string;
  email?: string;
  is_site_admin?: boolean;
  orgs?: OrgSummary[];
}

async function resolveOrgName(ctx: AppContext, orgId: string | null): Promise<string | null> {
  if (!orgId) return null;
  try {
    const me = await ctx.api.get<MeResponse>("/users/me", { requireOrg: false });
    return me.orgs?.find((o) => o.id === orgId)?.name ?? null;
  } catch {
    return null;
  }
}

export function registerSessionTools(server: McpServer, ctx: AppContext): void {
  defineTool(server, {
    name: "km2_login",
    title: "Sign in to KM2",
    description:
      "Open the KM2 web app in a browser and wait for you to complete the Clerk sign-in (SSO/MFA included). " +
      "If a session is already persisted this returns immediately. The session is reused for all other tools.",
    handler: async () => {
      const info = await ctx.session.login();
      if (!info.hasSession) {
        return (
          "Sign-in not completed within the timeout. Finish signing in in the opened browser window, " +
          "then call km2_login again (it will detect the session immediately)."
        );
      }
      const orgId = await ctx.session.getOrgId();
      const orgName = await resolveOrgName(ctx, orgId);
      return {
        signed_in: true,
        user: info.username ?? info.email ?? info.userId,
        email: info.email,
        active_org_id: orgId,
        active_org_name: orgName,
        note: orgId
          ? undefined
          : "Signed in, but no active organization is selected. Use km2_list_orgs then km2_set_org.",
      };
    },
  });

  defineTool(server, {
    name: "km2_status",
    title: "KM2 auth status",
    description:
      "Report the current sign-in state, the signed-in user, and the active organization (id + name). " +
      "Use this to check whether the agent can make API calls.",
    handler: async () => {
      const info = await ctx.session.status();
      const orgId = info.hasSession ? await ctx.session.getOrgId() : null;
      const orgName = await resolveOrgName(ctx, orgId);
      return {
        clerk_loaded: info.loaded,
        signed_in: info.hasSession,
        user: info.username ?? info.email ?? info.userId ?? null,
        email: info.email,
        active_org_id: orgId,
        active_org_name: orgName,
        app_url: ctx.cfg.appUrl,
        api_url: ctx.cfg.apiUrl,
        hint: !info.hasSession
          ? "Run km2_login to sign in."
          : !orgId
            ? "No active org — run km2_list_orgs then km2_set_org."
            : undefined,
      };
    },
  });

  defineTool(server, {
    name: "km2_list_orgs",
    title: "List my organizations",
    description: "List the organizations the signed-in user belongs to, and which one is currently active.",
    handler: async () => {
      const me = await ctx.api.get<MeResponse>("/users/me", { requireOrg: false });
      const activeOrgId = await ctx.session.getOrgId();
      return {
        active_org_id: activeOrgId,
        orgs: me.orgs ?? [],
      };
    },
  });

  defineTool(server, {
    name: "km2_set_org",
    title: "Set active organization",
    description:
      "Set which organization subsequent tools operate on. The id must be one of your orgs (see km2_list_orgs). " +
      "This becomes the X-Org-ID header for all org-scoped calls.",
    inputSchema: { org_id: uuid.describe("Organization UUID to activate") },
    handler: async ({ org_id }: { org_id: string }) => {
      const me = await ctx.api.get<MeResponse>("/users/me", { requireOrg: false });
      const match = me.orgs?.find((o) => o.id === org_id);
      await ctx.session.setOrgId(org_id);
      return {
        active_org_id: org_id,
        active_org_name: match?.name ?? null,
        warning: match ? undefined : "That org id is not in your membership list; API calls may return 403.",
      };
    },
  });
}
