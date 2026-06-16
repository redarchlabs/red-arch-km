import { Page, expect } from "@playwright/test";

/**
 * Page Object Model for the Documents page.
 * Covers document list view, creation, and navigation.
 */
export class DocumentsPage {
  constructor(private page: Page) {}

  async goto() {
    await this.page.goto("/documents");
    await this.page.waitForLoadState("networkidle");
  }

  async waitForDocumentsHeading() {
    await expect(
      this.page.getByRole("heading", { name: /documents/i }),
    ).toBeVisible({ timeout: 10_000 });
  }

  async clickNewDocument() {
    await this.page.getByRole("button", { name: /new document|create document/i }).click();
  }

  async fillDocumentForm(title: string, content: string) {
    await this.page.getByLabel(/title/i).fill(title);
    await this.page.getByLabel(/content|description/i).fill(content);
  }

  async submitDocument() {
    await this.page
      .getByRole("button", { name: /create|save/i })
      .first()
      .click();
  }

  async expectDocumentVisible(title: string) {
    await expect(
      this.page.getByText(title, { exact: false }),
    ).toBeVisible({ timeout: 10_000 });
  }

  async expectDocumentNotVisible(title: string) {
    await expect(
      this.page.getByText(title, { exact: false }),
    ).not.toBeVisible();
  }

  async clickDocument(title: string) {
    await this.page.getByText(title, { exact: false }).click();
  }

  async waitForDocumentDetail(title: string) {
    await expect(
      this.page.getByRole("heading", { name: title }),
    ).toBeVisible({ timeout: 10_000 });
  }

  async deleteDocument() {
    await this.page.getByRole("button", { name: /delete/i }).click();
    // Handle confirmation dialog if present
    const confirmButton = this.page.getByRole("button", { name: /confirm|yes|delete/i }).last();
    if (await confirmButton.isVisible({ timeout: 1000 })) {
      await confirmButton.click();
    }
  }

  async editDocument(newTitle?: string, newContent?: string) {
    await this.page.getByRole("button", { name: /edit/i }).click();
    if (newTitle) {
      const titleInput = this.page.getByLabel(/title/i);
      await titleInput.clear();
      await titleInput.fill(newTitle);
    }
    if (newContent) {
      const contentInput = this.page.getByLabel(/content|description/i);
      await contentInput.clear();
      await contentInput.fill(newContent);
    }
    await this.page.getByRole("button", { name: /save|update/i }).click();
  }

  async expectSuccess() {
    // Look for success toast/notification
    await expect(
      this.page.getByText(/success|saved|created/i),
    ).toBeVisible({ timeout: 5_000 });
  }
}
