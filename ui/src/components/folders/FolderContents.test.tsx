import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Document, Folder } from "@/types";

import { FolderContents } from "./FolderContents";

// The file browser reaches for these on mount (listDocuments) and from the
// context menu (updateFolder/updateDocument for Rename); mock the API layer so
// the component renders without a backend.
const listDocuments = vi.fn();
const updateDocument = vi.fn();
const updateFolder = vi.fn();

vi.mock("@/lib/api/documents", () => ({
  listDocuments: (...args: unknown[]) => listDocuments(...args),
  updateDocument: (...args: unknown[]) => updateDocument(...args),
  deleteDocument: vi.fn(),
}));

vi.mock("@/lib/api/folders", () => ({
  updateFolder: (...args: unknown[]) => updateFolder(...args),
  deleteFolder: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

// react-window's virtualizer observes container size; jsdom has no real layout,
// so provide a no-op ResizeObserver to keep it from throwing.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal("ResizeObserver", ResizeObserverStub);

const folder: Folder = {
  id: "f1",
  name: "Finance",
  description: null,
  parent_id: null,
  dot_path: "finance",
  order: 0,
  org_id: "o1",
  created_at: "2026-07-01T00:00:00Z",
  viewer_permissions_config: null,
  contributor_permissions_config: null,
};

function emptyPage() {
  return { items: [] as Document[], total: 0, page: 1, page_size: 200, pages: 1 };
}

function renderContents(overrides: Partial<React.ComponentProps<typeof FolderContents>> = {}) {
  const onOpenFolder = vi.fn();
  const onChanged = vi.fn();
  render(
    <FolderContents
      folder={folder}
      folders={[folder]}
      onOpenFolder={onOpenFolder}
      onChanged={onChanged}
      {...overrides}
    />,
  );
  return { onOpenFolder, onChanged };
}

/** Wait for the initial load to settle on the empty-folder state. */
async function waitForEmptyFolder() {
  return screen.findByText("This folder is empty.");
}

beforeEach(() => {
  listDocuments.mockResolvedValue(emptyPage());
  updateDocument.mockResolvedValue({});
  updateFolder.mockResolvedValue({});
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

describe("FolderContents whitespace context menu", () => {
  it("opens the folder background menu on a whitespace right-click", async () => {
    renderContents();
    const whitespace = await waitForEmptyFolder();

    fireEvent.contextMenu(whitespace);

    // The app menu — folder-level actions — replaces the browser's native menu.
    expect(screen.getByRole("menuitem", { name: "New subfolder" })).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: "New Markdown file" })).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: "Upload document here" })).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: "Rename" })).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: "Properties" })).toBeTruthy();
  });

  it("suppresses the browser's native context menu on whitespace right-click", async () => {
    renderContents();
    const whitespace = await waitForEmptyFolder();

    // dispatchEvent returns false when preventDefault() was called on a
    // cancelable event — proving the handler took over from the native menu.
    const notCancelled = fireEvent.contextMenu(whitespace);
    expect(notCancelled).toBe(false);
  });

  it("renders no whitespace menu until the folder is right-clicked", async () => {
    renderContents();
    await waitForEmptyFolder();
    expect(screen.queryByRole("menuitem")).toBeNull();
  });
});

describe("FolderContents rename", () => {
  it("renames the current folder via the whitespace menu", async () => {
    vi.spyOn(window, "prompt").mockReturnValue("Accounting");
    const { onChanged } = renderContents();
    const whitespace = await waitForEmptyFolder();

    fireEvent.contextMenu(whitespace);
    fireEvent.click(screen.getByRole("menuitem", { name: "Rename" }));

    await waitFor(() => expect(updateFolder).toHaveBeenCalledWith("f1", { name: "Accounting" }));
    // Success reloads the browser so the new name shows immediately.
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("rejects a folder name containing a dot", async () => {
    vi.spyOn(window, "prompt").mockReturnValue("a.b");
    const alert = vi.spyOn(window, "alert").mockImplementation(() => {});
    renderContents();
    const whitespace = await waitForEmptyFolder();

    fireEvent.contextMenu(whitespace);
    fireEvent.click(screen.getByRole("menuitem", { name: "Rename" }));

    await waitFor(() => expect(alert).toHaveBeenCalledWith("Folder names cannot contain '.'"));
    expect(updateFolder).not.toHaveBeenCalled();
  });

  it("does nothing when the rename prompt is cancelled", async () => {
    vi.spyOn(window, "prompt").mockReturnValue(null);
    renderContents();
    const whitespace = await waitForEmptyFolder();

    fireEvent.contextMenu(whitespace);
    fireEvent.click(screen.getByRole("menuitem", { name: "Rename" }));

    expect(updateFolder).not.toHaveBeenCalled();
  });

  it("does nothing when the name is unchanged", async () => {
    vi.spyOn(window, "prompt").mockReturnValue("Finance");
    renderContents();
    const whitespace = await waitForEmptyFolder();

    fireEvent.contextMenu(whitespace);
    fireEvent.click(screen.getByRole("menuitem", { name: "Rename" }));

    expect(updateFolder).not.toHaveBeenCalled();
  });
});
