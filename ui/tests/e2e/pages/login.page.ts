import { Page, expect } from "@playwright/test";

/**
 * Page Object Model for the Login page.
 * Handles Keycloak login flow and session validation.
 */
export class LoginPage {
  constructor(private page: Page) {}

  async goto() {
    await this.page.goto("/login");
  }

  async waitForLoginPage() {
    await this.page.waitForLoadState("domcontentloaded");
  }

  async isAtLoginPage() {
    const url = this.page.url();
    return /login|keycloak|auth/i.test(url);
  }

  async expectPageTitle() {
    await expect(this.page).toHaveTitle(/Red Arch/i);
  }

  async clickLoginWithKeycloak() {
    // Typically handled via Keycloak redirect
    // This POM can be extended with actual login flow when Keycloak is fully mocked
    await this.page.getByRole("button", { name: /login|signin/i }).click();
  }
}
