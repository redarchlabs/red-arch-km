import { Page, expect } from "@playwright/test";

/**
 * Page Object Model for the Chat/RAG panel.
 * Covers message sending, streaming responses, and knowledge base integration.
 */
export class ChatPage {
  constructor(private page: Page) {}

  async goto() {
    await this.page.goto("/chat");
    await this.page.waitForLoadState("networkidle");
  }

  async waitForChatInterface() {
    await expect(
      this.page.locator("[class*='chat'], input[placeholder*='message' i]"),
    ).toBeVisible({ timeout: 10_000 });
  }

  async getMessageInput() {
    return this.page.locator("input[placeholder*='message' i], textarea[placeholder*='message' i]").first();
  }

  async sendMessage(text: string) {
    const input = await this.getMessageInput();
    await input.fill(text);
    await this.page.getByRole("button", { name: /send|submit/i }).click();
  }

  async waitForResponse(timeout: number = 30_000) {
    // Look for message from assistant
    const assistantMessage = this.page.locator("[class*='message'], [class*='assistant']").last();
    await assistantMessage.waitFor({ state: "visible", timeout });
  }

  async expectResponseContains(text: string) {
    // Check if recent message contains expected text
    const messages = this.page.locator("[class*='message'], p").all();
    let found = false;
    for (const msg of await messages) {
      const content = await msg.textContent();
      if (content?.includes(text)) {
        found = true;
        break;
      }
    }
    expect(found).toBe(true);
  }

  async expectStreamingIndicator() {
    // Look for loading/typing indicator during streaming
    await expect(
      this.page.locator("[class*='loading'], [class*='typing'], [class*='streaming']"),
    ).toBeVisible({ timeout: 5_000 });
  }

  async clearChatHistory() {
    const clearButton = this.page.getByRole("button", { name: /clear|reset|new chat/i });
    if (await clearButton.isVisible({ timeout: 1000 })) {
      await clearButton.click();
    }
  }

  async attachDocument(documentPath?: string) {
    const attachButton = this.page.getByRole("button", { name: /attach|upload|file/i });
    if (await attachButton.isVisible({ timeout: 1000 })) {
      await attachButton.click();
      if (documentPath) {
        // Handle file upload if needed
        await this.page.setInputFiles("input[type='file']", documentPath);
      }
    }
  }
}
