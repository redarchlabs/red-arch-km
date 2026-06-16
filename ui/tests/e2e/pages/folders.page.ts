import { Page, expect } from "@playwright/test";

/**
 * Page Object Model for the Folders page.
 * Covers folder creation, navigation, and document organization.
 */
export class FoldersPage {
  constructor(private page: Page) {}

  async goto() {
    await this.page.goto("/folders");
    await this.page.waitForLoadState("networkidle");
  }

  async waitForFoldersHeading() {
    await expect(
      this.page.getByRole("heading", { name: /folders/i }),
    ).toBeVisible({ timeout: 10_000 });
  }

  async clickNewFolder() {
    await this.page.getByRole("button", { name: /new folder|create folder/i }).click();
  }

  async fillFolderForm(name: string, description?: string) {
    await this.page.getByLabel(/name|folder name/i).fill(name);
    if (description) {
      const descLabel = this.page.getByLabel(/description/i);
      if (await descLabel.isVisible({ timeout: 1000 })) {
        await descLabel.fill(description);
      }
    }
  }

  async submitFolder() {
    await this.page
      .getByRole("button", { name: /create|save/i })
      .first()
      .click();
  }

  async expectFolderVisible(name: string) {
    await expect(
      this.page.getByText(name, { exact: false }),
    ).toBeVisible({ timeout: 10_000 });
  }

  async clickFolder(name: string) {
    await this.page.getByText(name, { exact: false }).click();
  }

  async moveDocumentToFolder(documentName: string, folderName: string) {
    // Typically drag-and-drop or context menu
    // Find document and open move dialog/menu
    const docElement = this.page.getByText(documentName);
    await docElement.click({ button: "right" });

    // Click "Move to folder" if available
    const moveButton = this.page.getByText(/move|move to/i);
    if (await moveButton.isVisible({ timeout: 1000 })) {
      await moveButton.click();
    }

    // Select target folder
    const folderOption = this.page.getByText(folderName);
    if (await folderOption.isVisible({ timeout: 1000 })) {
      await folderOption.click();
    }
  }

  async expectSuccess() {
    await expect(
      this.page.getByText(/success|saved|created|moved/i),
    ).toBeVisible({ timeout: 5_000 });
  }
}
