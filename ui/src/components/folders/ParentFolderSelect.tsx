"use client";

import type { Folder } from "@/types";

interface ParentFolderSelectProps {
  value: string | null;
  onChange: (parentId: string | null) => void;
  folders: Folder[];
  /** Hide this folder and its descendants from the options (used when editing). */
  excludeId?: string;
  disabled?: boolean;
  id?: string;
}

export function ParentFolderSelect({
  value,
  onChange,
  folders,
  excludeId,
  disabled,
  id,
}: ParentFolderSelectProps) {
  const excluded = new Set<string>();
  if (excludeId) {
    const excludedFolder = folders.find((f) => f.id === excludeId);
    const prefix = excludedFolder?.dot_path;
    for (const folder of folders) {
      if (
        folder.id === excludeId ||
        (prefix && (folder.dot_path === prefix || folder.dot_path.startsWith(`${prefix}.`)))
      ) {
        excluded.add(folder.id);
      }
    }
  }

  const options = folders
    .filter((f) => !excluded.has(f.id))
    .sort((a, b) => a.dot_path.localeCompare(b.dot_path));

  return (
    <select
      id={id}
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value || null)}
      disabled={disabled}
      className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
    >
      <option value="">— Root (no parent) —</option>
      {options.map((folder) => (
        <option key={folder.id} value={folder.id}>
          {folder.dot_path}
        </option>
      ))}
    </select>
  );
}
