"use client";

import { Check, ChevronDown, Search, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { cn } from "@/lib/utils";
import type { Folder, Tag } from "@/types";

interface ScopeSelectorProps {
  folders: Folder[];
  tags: Tag[];
  selectedFolderIds: string[];
  selectedTagIds: string[];
  onChangeFolders: (ids: string[]) => void;
  onChangeTags: (ids: string[]) => void;
  disabled?: boolean;
}

function toggle(ids: string[], id: string): string[] {
  return ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id];
}

/**
 * Searchable multi-select for scoping a chat's document retrieval to any
 * combination of folders and tags. Selecting nothing means "All documents".
 *
 * Built from primitives (button + filtered checklist panel) because the UI
 * kit has no combobox/popover component.
 */
export function ScopeSelector({
  folders,
  tags,
  selectedFolderIds,
  selectedTagIds,
  onChangeFolders,
  onChangeTags,
  disabled = false,
}: ScopeSelectorProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);

  // Close on outside click / Escape so the panel behaves like a menu.
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const needle = query.trim().toLowerCase();
  const visibleFolders = useMemo(
    () =>
      needle
        ? folders.filter((f) => (f.dot_path || f.name).toLowerCase().includes(needle))
        : folders,
    [folders, needle],
  );
  const visibleTags = useMemo(
    () => (needle ? tags.filter((t) => t.name.toLowerCase().includes(needle)) : tags),
    [tags, needle],
  );

  const totalSelected = selectedFolderIds.length + selectedTagIds.length;
  const label =
    totalSelected === 0
      ? "All documents"
      : `${totalSelected} scope${totalSelected === 1 ? "" : "s"}`;

  const clearAll = () => {
    onChangeFolders([]);
    onChangeTags([]);
  };

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className="flex h-9 min-w-[11rem] items-center justify-between gap-2 rounded-md border border-input bg-transparent px-2 py-1 text-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-50"
      >
        <span className={cn(totalSelected === 0 && "text-muted-foreground")}>{label}</span>
        <ChevronDown className="h-4 w-4 shrink-0 opacity-60" />
      </button>

      {open ? (
        <div className="absolute right-0 z-20 mt-1 w-72 rounded-md border bg-background p-2 shadow-md">
          <div className="mb-2 flex items-center gap-2 rounded-md border border-input px-2">
            <Search className="h-4 w-4 shrink-0 text-muted-foreground" />
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search folders and tags…"
              className="h-8 w-full bg-transparent text-sm focus-visible:outline-none"
            />
            {totalSelected > 0 ? (
              <button
                type="button"
                onClick={clearAll}
                title="Clear scope"
                aria-label="Clear scope"
                className="shrink-0 rounded p-0.5 text-muted-foreground hover:text-foreground"
              >
                <X className="h-4 w-4" />
              </button>
            ) : null}
          </div>

          <div className="max-h-64 overflow-y-auto">
            {visibleFolders.length === 0 && visibleTags.length === 0 ? (
              <p className="px-2 py-3 text-center text-sm text-muted-foreground">No matches.</p>
            ) : null}

            {visibleFolders.length > 0 ? (
              <>
                <p className="px-2 py-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  Folders
                </p>
                {visibleFolders.map((f) => {
                  const checked = selectedFolderIds.includes(f.id);
                  return (
                    <ScopeOption
                      key={f.id}
                      label={f.dot_path || f.name}
                      checked={checked}
                      onToggle={() => onChangeFolders(toggle(selectedFolderIds, f.id))}
                    />
                  );
                })}
              </>
            ) : null}

            {visibleTags.length > 0 ? (
              <>
                <p className="mt-1 px-2 py-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  Tags
                </p>
                {visibleTags.map((t) => {
                  const checked = selectedTagIds.includes(t.id);
                  return (
                    <ScopeOption
                      key={t.id}
                      label={t.name}
                      checked={checked}
                      onToggle={() => onChangeTags(toggle(selectedTagIds, t.id))}
                    />
                  );
                })}
              </>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}

interface ScopeOptionProps {
  label: string;
  checked: boolean;
  onToggle: () => void;
}

function ScopeOption({ label, checked, onToggle }: ScopeOptionProps) {
  return (
    <button
      type="button"
      role="option"
      aria-selected={checked}
      onClick={onToggle}
      className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm hover:bg-accent/50"
    >
      <span
        className={cn(
          "flex h-4 w-4 shrink-0 items-center justify-center rounded border",
          checked ? "border-primary bg-primary text-primary-foreground" : "border-input",
        )}
      >
        {checked ? <Check className="h-3 w-3" /> : null}
      </span>
      <span className="truncate">{label}</span>
    </button>
  );
}
