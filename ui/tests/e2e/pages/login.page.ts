import { Page, expect } from "@playwright/test";

/**
 * Page Object Model for the Login page.
 * Handles the Clerk sign-in flow and session validation.
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
    return /login|sign-in|clerk|auth/i.test(url);
  }

  async expectPageTitle() {
    await expect(this.page).toHaveTitle(/Red Arch/i);
  }

  async clickSignIn() {
    // The Clerk <SignIn/> component renders the sign-in form; this POM can be
    // extended with the full flow via @clerk/testing when live login is exercised.
    await this.page.getByRole("button", { name: /login|signin|sign in/i }).click();
  }
}
