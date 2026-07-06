import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Folder, Tag } from "@/types";

import { ScopeSelector } from "./ScopeSelector";

const folders: Folder[] = [
  {
    id: "f1",
    name: "Sales",
    description: null,
    parent_id: null,
    dot_path: "Sales",
    order: 0,
    org_id: "o1",
    created_at: null,
    viewer_permissions_config: null,
    contributor_permissions_config: null,
  },
  {
    id: "f2",
    name: "Product",
    description: null,
    parent_id: null,
    dot_path: "Product",
    order: 1,
    org_id: "o1",
    created_at: null,
    viewer_permissions_config: null,
    contributor_permissions_config: null,
  },
];

const tags: Tag[] = [
  { id: "t1", name: "pricing" },
  { id: "t2", name: "roadmap" },
];

function renderSelector(overrides: Partial<React.ComponentProps<typeof ScopeSelector>> = {}) {
  const onChangeFolders = vi.fn();
  const onChangeTags = vi.fn();
  render(
    <ScopeSelector
      folders={folders}
      tags={tags}
      selectedFolderIds={[]}
      selectedTagIds={[]}
      onChangeFolders={onChangeFolders}
      onChangeTags={onChangeTags}
      {...overrides}
    />,
  );
  return { onChangeFolders, onChangeTags };
}

afterEach(cleanup);

describe("ScopeSelector", () => {
  it("shows 'All documents' when nothing is selected", () => {
    renderSelector();
    expect(screen.getByRole("button", { name: /all documents/i })).toBeTruthy();
  });

  it("summarizes the count when scopes are selected", () => {
    renderSelector({ selectedFolderIds: ["f1"], selectedTagIds: ["t1"] });
    expect(screen.getByRole("button", { name: /2 scopes/i })).toBeTruthy();
  });

  it("reveals folders and tags when opened", () => {
    renderSelector();
    fireEvent.click(screen.getByRole("button", { name: /all documents/i }));
    expect(screen.getByRole("option", { name: "Sales" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "pricing" })).toBeTruthy();
  });

  it("toggles a folder selection via its callback", () => {
    const { onChangeFolders } = renderSelector({ selectedFolderIds: ["f1"] });
    fireEvent.click(screen.getByRole("button", { name: /1 scope/i }));
    fireEvent.click(screen.getByRole("option", { name: "Product" }));
    expect(onChangeFolders).toHaveBeenCalledWith(["f1", "f2"]);
  });

  it("removes an already-selected folder when toggled off", () => {
    const { onChangeFolders } = renderSelector({ selectedFolderIds: ["f1"] });
    fireEvent.click(screen.getByRole("button", { name: /1 scope/i }));
    fireEvent.click(screen.getByRole("option", { name: "Sales" }));
    expect(onChangeFolders).toHaveBeenCalledWith([]);
  });

  it("filters options by the search query across folders and tags", () => {
    renderSelector();
    fireEvent.click(screen.getByRole("button", { name: /all documents/i }));
    fireEvent.change(screen.getByPlaceholderText(/search folders and tags/i), {
      target: { value: "pric" },
    });
    expect(screen.queryByRole("option", { name: "Sales" })).toBeNull();
    expect(screen.getByRole("option", { name: "pricing" })).toBeTruthy();
  });

  it("clears all selections via the clear control", () => {
    const { onChangeFolders, onChangeTags } = renderSelector({
      selectedFolderIds: ["f1"],
      selectedTagIds: ["t1"],
    });
    fireEvent.click(screen.getByRole("button", { name: /2 scopes/i }));
    fireEvent.click(screen.getByRole("button", { name: /clear scope/i }));
    expect(onChangeFolders).toHaveBeenCalledWith([]);
    expect(onChangeTags).toHaveBeenCalledWith([]);
  });

  it("marks the selected option with the option's aria-selected state", () => {
    renderSelector({ selectedTagIds: ["t1"] });
    fireEvent.click(screen.getByRole("button", { name: /1 scope/i }));
    const pricing = screen.getByRole("option", { name: "pricing" });
    expect(pricing.getAttribute("aria-selected")).toBe("true");
    const roadmap = within(pricing.parentElement as HTMLElement).getByRole("option", {
      name: "roadmap",
    });
    expect(roadmap.getAttribute("aria-selected")).toBe("false");
  });
});
