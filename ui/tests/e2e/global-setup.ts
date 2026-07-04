import { type FullConfig, request } from "@playwright/test";
import * as fs from "node:fs/promises";
import * as path from "node:path";

/**
 * Global setup for E2E tests.
 *
 * Connects to the seeded API as the E2E test admin, fetches the org ID,
 * and writes a storageState.json that subsequent tests consume. This
 * avoids each test having to re-authenticate.
 *
 * Required env vars when E2E_WITH_BACKEND=1:
 *   - E2E_API_URL    (default http://localhost:8000)
 *   - E2E_UI_URL     (default http://localhost:3000)
 *   - E2E_TEST_SECRET (must match API_E2E_TEST_SECRET server-side)
 *   - E2E_TEST_USER  (default "e2e_admin:e2e_admin@e2e.local")
 */
async function globalSetup(_config: FullConfig) {
  if (!process.env.E2E_WITH_BACKEND) {
    return;
  }

  const apiUrl = process.env.E2E_API_URL ?? "http://localhost:8000";
  const uiUrl = process.env.E2E_UI_URL ?? "http://localhost:3000";
  const secret = process.env.E2E_TEST_SECRET ?? "";
  const testUser = process.env.E2E_TEST_USER ?? "e2e_admin:e2e_admin@e2e.local";

  if (!secret) {
    throw new Error("E2E_TEST_SECRET is required when E2E_WITH_BACKEND=1");
  }

  const ctx = await request.newContext({
    baseURL: apiUrl,
    extraHTTPHeaders: {
      "X-Test-User": testUser,
      "X-Test-Secret": secret,
    },
  });

  const meRes = await ctx.get("/api/users/me");
  if (!meRes.ok()) {
    throw new Error(`seed check failed: ${meRes.status()} ${await meRes.text()}`);
  }
  const me = (await meRes.json()) as { orgs: Array<{ id: string; name: string }> };
  const org = me.orgs.find((o) => o.name === "E2E Test Org") ?? me.orgs[0];
  if (!org) {
    throw new Error("No orgs accessible to test admin — run seed_e2e.py first");
  }

  // Persist the state for tests to pick up — Playwright stores cookies + origins,
  // but we also stash our own values the tests read from process.env-style globals.
  // Resolve the same way fixtures.ts reads it (cwd-relative, cwd = ui/), so the
  // writer and reader agree. Using config.rootDir here doubled the path
  // (rootDir is the testDir tests/e2e → tests/e2e/tests/e2e/.state.json).
  const statePath = path.resolve(
    process.env.E2E_STATE_FILE ?? "tests/e2e/.state.json",
  );
  await fs.writeFile(
    statePath,
    JSON.stringify({
      orgId: org.id,
      testUser,
      testSecret: secret,
      apiUrl,
      uiUrl,
    }),
    "utf-8",
  );

  await ctx.dispose();
}

export default globalSetup;
