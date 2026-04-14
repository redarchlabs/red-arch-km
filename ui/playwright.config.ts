import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright configuration for E2E tests.
 *
 * Tests run against the dev server unless `BASE_URL` is set to point at
 * a production or staging deployment. CI runs with `headless` enabled and
 * records traces on first retry for debugging.
 */
export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 30_000,
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 2 : undefined,
  reporter: process.env.CI ? "github" : "list",
  // Only run the full-journey setup when a seeded backend is available.
  // Without it, the basic smoke tests still run.
  globalSetup: process.env.E2E_WITH_BACKEND ? "./tests/e2e/global-setup.ts" : undefined,

  use: {
    baseURL: process.env.BASE_URL ?? "http://localhost:3000",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],

  webServer: process.env.BASE_URL
    ? undefined
    : {
        command: "npm run dev",
        url: "http://localhost:3000",
        reuseExistingServer: !process.env.CI,
        timeout: 60_000,
      },
});
