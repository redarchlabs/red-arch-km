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
});
