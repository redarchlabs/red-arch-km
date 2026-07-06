import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ChatSession } from "@/types";

import { SessionList } from "./SessionList";

const sessions: ChatSession[] = [
  {
    id: "s1",
    chat_data: { messages: [{ content: "What is our release process?" }] },
    created_at: "2026-07-01T00:00:00Z",
    updated_at: "2026-07-01T00:00:00Z",
  },
];

function renderList(overrides: Partial<React.ComponentProps<typeof SessionList>> = {}) {
  const onSelect = vi.fn();
  const onNew = vi.fn();
  const onDelete = vi.fn();
  render(
    <SessionList
      sessions={sessions}
      activeId={null}
      onSelect={onSelect}
      onNew={onNew}
      onDelete={onDelete}
      {...overrides}
    />,
  );
  return { onSelect, onNew, onDelete };
}

afterEach(cleanup);

describe("SessionList", () => {
  it("calls onDelete (and not onSelect) when the delete control is clicked", () => {
    const { onSelect, onDelete } = renderList();
    fireEvent.click(screen.getByRole("button", { name: /delete conversation/i }));
    expect(onDelete).toHaveBeenCalledWith("s1");
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("calls onSelect when the conversation row is clicked", () => {
    const { onSelect } = renderList();
    fireEvent.click(screen.getByText("What is our release process?"));
    expect(onSelect).toHaveBeenCalledWith("s1");
  });
});
