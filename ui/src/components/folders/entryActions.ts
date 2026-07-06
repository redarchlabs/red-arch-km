import { deleteDocument, updateDocument } from "@/lib/api/documents";
import { deleteFolder, updateFolder } from "@/lib/api/folders";

/**
 * Prompt for a new name and rename a folder or document in place. Uses the same
 * lightweight browser dialogs as {@link deleteEntry}'s confirm(); a
 * null/blank/unchanged name is a no-op. Folder names cannot contain "." (matches
 * FolderProperties' rule). A failed request surfaces via `alert` instead of
 * bubbling up as an unhandled rejection.
 */
export async function renameEntry(
  kind: "folder" | "doc",
  id: string,
  currentName: string,
  onDone: () => void,
): Promise<void> {
  const next = window.prompt(`Rename "${currentName}" to:`, currentName);
  if (next == null) return;
  const trimmed = next.trim();
  if (!trimmed || trimmed === currentName) return;
  if (kind === "folder" && trimmed.includes(".")) {
    window.alert("Folder names cannot contain '.'");
    return;
  }
  try {
    if (kind === "folder") await updateFolder(id, { name: trimmed });
    else await updateDocument(id, { title: trimmed });
    onDone();
  } catch (e) {
    window.alert(e instanceof Error ? e.message : "Rename failed");
  }
}

/**
 * Confirm and delete a folder or document. Mirrors {@link renameEntry}: the
 * request is wrapped in try/catch so a 409/403/network failure surfaces via
 * `alert` (rather than an unhandled rejection from the fire-and-forget menu
 * `onSelect`), and `onDone` only fires on success.
 */
export async function deleteEntry(
  kind: "folder" | "doc",
  id: string,
  name: string,
  onDone: () => void,
): Promise<void> {
  const message =
    kind === "folder"
      ? `Delete folder "${name}"? Documents inside are not deleted.`
      : `Delete "${name}" permanently?`;
  if (!window.confirm(message)) return;
  try {
    if (kind === "folder") await deleteFolder(id);
    else await deleteDocument(id);
    onDone();
  } catch (e) {
    window.alert(e instanceof Error ? e.message : "Delete failed");
  }
}
