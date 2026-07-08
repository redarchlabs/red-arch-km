/**
 * Runtime configuration for the KM2 MCP server.
 *
 * Everything is env-driven with dev-friendly defaults. Crucially, NO secrets
 * are read or stored here: the server authenticates purely by riding the
 * user's own Clerk browser session (see BrowserSession), so the only sensitive
 * artifact on disk is the Playwright profile directory (session cookies), which
 * is created 0700 and gitignored.
 */
import os from "node:os";
import path from "node:path";

function env(name: string, fallback: string): string {
  const v = process.env[name];
  return v === undefined || v === "" ? fallback : v;
}

function envBool(name: string, fallback: boolean): boolean {
  const v = process.env[name];
  if (v === undefined || v === "") return fallback;
  return /^(1|true|yes|on)$/i.test(v);
}

function envInt(name: string, fallback: number): number {
  const v = process.env[name];
  if (v === undefined || v === "") return fallback;
  const n = Number.parseInt(v, 10);
  return Number.isFinite(n) ? n : fallback;
}

export interface Config {
  /** URL of the running KM2 web app — the Clerk-configured origin we harvest tokens from. */
  appUrl: string;
  /** Base URL of the KM2 backend API (includes the /api prefix). */
  apiUrl: string;
  /** Optional Clerk JWT template name; mirror NEXT_PUBLIC_CLERK_JWT_TEMPLATE if the app sets one. */
  clerkJwtTemplate: string | undefined;
  /** localStorage key the app persists the active org id under. */
  orgStorageKey: string;
  /** Optional hard override for X-Org-ID; when set, wins over the app's localStorage value. */
  orgIdOverride: string | undefined;
  /** Persistent Playwright user-data dir (holds the Clerk session). */
  userDataDir: string;
  /**
   * Browser channel to drive (e.g. "chrome", "msedge"). Undefined uses
   * Playwright's bundled Chromium. Real Chrome is far less likely to trip
   * IdP bot-detection (GoDaddy/M365's "your browser is a bit unusual" wall).
   */
  browserChannel: string | undefined;
  /** Run the browser headless. Login should be headed; reuse can be headless. */
  headless: boolean;
  /** How long km2_login waits for the user to finish signing in, in ms. */
  loginTimeoutMs: number;
}

export function loadConfig(): Config {
  return {
    appUrl: env("KM2_APP_URL", "http://localhost:3000").replace(/\/+$/, ""),
    apiUrl: env("KM2_API_URL", "http://localhost:8000/api").replace(/\/+$/, ""),
    clerkJwtTemplate: process.env.KM2_CLERK_JWT_TEMPLATE || undefined,
    orgStorageKey: env("KM2_ORG_STORAGE_KEY", "redarch:currentOrgId"),
    orgIdOverride: process.env.KM2_ORG_ID || undefined,
    userDataDir: env("KM2_USER_DATA_DIR", path.join(os.homedir(), ".km2-mcp", "profile")),
    browserChannel: process.env.KM2_BROWSER_CHANNEL || undefined,
    headless: envBool("KM2_HEADLESS", false),
    loginTimeoutMs: envInt("KM2_LOGIN_TIMEOUT_MS", 180_000),
  };
}
