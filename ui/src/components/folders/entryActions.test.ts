import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { deleteEntry, renameEntry } from "./entryActions";

const deleteDocument = vi.fn();
const updateDocument = vi.fn();
const deleteFolder = vi.fn();
const updateFolder = vi.fn();

vi.mock("@/lib/api/documents", () => ({
  deleteDocument: (...a: unknown[]) => deleteDocument(...a),
  updateDocument: (...a: unknown[]) => updateDocument(...a),
}));
vi.mock("@/lib/api/folders", () => ({
  deleteFolder: (...a: unknown[]) => deleteFolder(...a),
  updateFolder: (...a: unknown[]) => updateFolder(...a),
}));

beforeEach(() => {
  deleteDocument.mockResolvedValue(undefined);
  updateDocument.mockResolvedValue({});
  deleteFolder.mockResolvedValue(undefined);
  updateFolder.mockResolvedValue({});
});

afterEach(() => {
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

describe("deleteEntry", () => {
  it("deletes a document and reloads when confirmed", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const onDone = vi.fn();

    await deleteEntry("doc", "d1", "Budget.md", onDone);

    expect(deleteDocument).toHaveBeenCalledWith("d1");
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("deletes a folder with the folder confirm copy", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const onDone = vi.fn();

    await deleteEntry("folder", "f1", "Finance", onDone);

    expect(confirm).toHaveBeenCalledWith(
      'Delete folder "Finance"? Documents inside are not deleted.',
    );
    expect(deleteFolder).toHaveBeenCalledWith("f1");
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("does nothing when the confirm is cancelled", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    const onDone = vi.fn();

    await deleteEntry("doc", "d1", "Budget.md", onDone);

    expect(deleteDocument).not.toHaveBeenCalled();
    expect(onDone).not.toHaveBeenCalled();
  });

  it("surfaces a rejected delete via alert without throwing or reloading", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const alert = vi.spyOn(window, "alert").mockImplementation(() => {});
    deleteDocument.mockRejectedValue(new Error("409 Conflict: folder not empty"));
    const onDone = vi.fn();

    // Must resolve (not reject) — a fire-and-forget menu onSelect would otherwise
    // become an unhandled rejection with no user feedback.
    await expect(deleteEntry("doc", "d1", "Budget.md", onDone)).resolves.toBeUndefined();

    expect(alert).toHaveBeenCalledWith("409 Conflict: folder not empty");
    expect(onDone).not.toHaveBeenCalled();
  });
});

describe("renameEntry", () => {
  it("renames a document and reloads", async () => {
    vi.spyOn(window, "prompt").mockReturnValue("Renamed.md");
    const onDone = vi.fn();

    await renameEntry("doc", "d1", "Budget.md", onDone);

    expect(updateDocument).toHaveBeenCalledWith("d1", { title: "Renamed.md" });
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("surfaces a rejected rename via alert without throwing", async () => {
    vi.spyOn(window, "prompt").mockReturnValue("Renamed.md");
    const alert = vi.spyOn(window, "alert").mockImplementation(() => {});
    updateDocument.mockRejectedValue(new Error("403 Forbidden"));
    const onDone = vi.fn();

    await expect(renameEntry("doc", "d1", "Budget.md", onDone)).resolves.toBeUndefined();

    expect(alert).toHaveBeenCalledWith("403 Forbidden");
    expect(onDone).not.toHaveBeenCalled();
  });

  it("rejects a folder rename containing a dot", async () => {
    vi.spyOn(window, "prompt").mockReturnValue("a.b");
    const alert = vi.spyOn(window, "alert").mockImplementation(() => {});
    const onDone = vi.fn();

    await renameEntry("folder", "f1", "Finance", onDone);

    expect(alert).toHaveBeenCalledWith("Folder names cannot contain '.'");
    expect(updateFolder).not.toHaveBeenCalled();
    expect(onDone).not.toHaveBeenCalled();
  });
});
