import { Page, expect } from "@playwright/test";

/**
 * Page Object Model for the global Site Admin console (/site-admin).
 */
export class SiteAdminPage {
  constructor(private page: Page) {}

  async goto() {
    await this.page.goto("/site-admin");
    await this.page.waitForLoadState("networkidle");
  }

  async waitForConsole() {
    await expect(
      this.page.getByRole("heading", { name: /site admin/i }),
    ).toBeVisible({ timeout: 10_000 });
  }

  async openTab(name: "Organizations" | "Users" | "Memberships" | "System") {
    await this.page.getByRole("tab", { name }).click();
  }

  async expectAccessDenied() {
    await expect(
      this.page.getByText(/site admin access required/i),
    ).toBeVisible({ timeout: 5_000 });
  }

  async expectOrgListed(orgName: string) {
    await expect(this.page.getByText(orgName, { exact: false })).toBeVisible({
      timeout: 10_000,
    });
  }
}
