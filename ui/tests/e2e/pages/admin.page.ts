import { Page, expect } from "@playwright/test";

/**
 * Page Object Model for the Admin page.
 * Covers RBAC controls and permission management.
 */
export class AdminPage {
  constructor(private page: Page) {}

  async goto() {
    await this.page.goto("/admin");
    await this.page.waitForLoadState("networkidle");
  }

  async waitForAdminPanel() {
    await expect(
      this.page.getByRole("heading", { name: /admin|settings/i }),
    ).toBeVisible({ timeout: 10_000 });
  }

  async expectAccessDenied() {
    await expect(
      this.page.getByText(/access denied|unauthorized|forbidden/i),
    ).toBeVisible({ timeout: 5_000 });
  }

  async expectAdminContent() {
    await expect(
      this.page.getByText(/users|roles|permissions|teams/i),
    ).toBeVisible({ timeout: 5_000 });
  }

  async grantUserPermission(username: string, role: string) {
    const userRow = this.page.getByText(username).first();
    await userRow.click();

    const roleSelect = this.page.getByLabel(/role|permission/i);
    if (await roleSelect.isVisible({ timeout: 1000 })) {
      await roleSelect.selectOption(role);
    }

    await this.page.getByRole("button", { name: /save|apply/i }).click();
  }

  async revokeUserPermission(username: string) {
    const userRow = this.page.getByText(username).first();
    const revokeButton = userRow.locator("..").getByRole("button", { name: /remove|revoke|delete/i });
    if (await revokeButton.isVisible({ timeout: 1000 })) {
      await revokeButton.click();
    }
  }

  async expectSuccess() {
    await expect(
      this.page.getByText(/success|updated|saved/i),
    ).toBeVisible({ timeout: 5_000 });
  }
}
