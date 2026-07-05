import { describe, expect, it } from "vitest";

import { htmlToMarkdown } from "./markdown";

describe("htmlToMarkdown", () => {
  it("converts headings to ATX Markdown", () => {
    expect(htmlToMarkdown("<h2>Title</h2>")).toBe("## Title");
  });

  it("converts bold and italic", () => {
    expect(htmlToMarkdown("<p><strong>bold</strong> and <em>italic</em></p>")).toBe(
      "**bold** and *italic*",
    );
  });

  it("converts bullet lists with a dash marker", () => {
    const md = htmlToMarkdown("<ul><li>one</li><li>two</li></ul>");
    // Turndown pads list items ("-   one"); assert the dash marker + item.
    expect(md).toMatch(/-\s+one/);
    expect(md).toMatch(/-\s+two/);
  });

  it("strips wrapper tags from plain paragraphs", () => {
    expect(htmlToMarkdown("<p>just text</p>")).toBe("just text");
  });

  it("returns empty string for an empty editor document", () => {
    expect(htmlToMarkdown("<p></p>")).toBe("");
  });
});
