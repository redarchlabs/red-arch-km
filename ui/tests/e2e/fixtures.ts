import { test as base, type APIRequestContext, request } from "@playwright/test";
import * as fs from "node:fs/promises";
import * as path from "node:path";

interface E2EState {
  orgId: string;
  testUser: string;
  testSecret: string;
  apiUrl: string;
  uiUrl: string;
}

interface E2EFixtures {
  e2eState: E2EState;
  apiContext: APIRequestContext;
}

async function readState(): Promise<E2EState> {
  const statePath = path.resolve(
    process.env.E2E_STATE_FILE ?? "tests/e2e/.state.json",
  );
  const raw = await fs.readFile(statePath, "utf-8");
  return JSON.parse(raw) as E2EState;
}

export const test = base.extend<E2EFixtures>({
  e2eState: async ({}, use) => {
    const state = await readState();
    await use(state);
  },
  apiContext: async ({ e2eState }, use) => {
    const ctx = await request.newContext({
      baseURL: e2eState.apiUrl,
      extraHTTPHeaders: {
        "X-Test-User": e2eState.testUser,
        "X-Test-Secret": e2eState.testSecret,
        "X-Org-ID": e2eState.orgId,
      },
    });
    await use(ctx);
    await ctx.dispose();
  },
});

export { expect } from "@playwright/test";
