import { expect, test } from "@playwright/test";

/**
 * Authenticated document flow.
 *
 * These tests require a real backend with a seeded user. They
 * assume `storageState.json` has been populated by a global setup
 * script (see playwright.config.ts `globalSetup` when implemented).
 * Without a real backend + auth, mark as skipped.
 */

test.describe("documents page", () => {
  test.skip(!process.env.E2E_WITH_BACKEND, "requires seeded backend");

  test("can create and view a document", async ({ page }) => {
    await page.goto("/documents");
    await expect(page.getByRole("heading", { name: "Documents" })).toBeVisible();

    await page.getByRole("button", { name: /new document/i }).click();
    await page.getByLabel("Title").fill("E2E Test Document");
    await page.getByLabel("Content").fill("This is a test document created by Playwright.");
    await page.getByRole("button", { name: "Create" }).click();

    await expect(page.getByText("E2E Test Document")).toBeVisible();

    // Navigate into detail view
    await page.getByText("E2E Test Document").click();
    await expect(page.getByRole("heading", { name: "E2E Test Document" })).toBeVisible();
  });
});
