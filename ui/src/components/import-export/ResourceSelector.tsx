"use client";

import { ChevronDown, ChevronRight } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import type { Selection } from "@/lib/api/migration";

import type { SelectableGroup, SelectableItem } from "./selection";

interface ResourceSelectorProps {
  groups: SelectableGroup[];
  selection: Selection;
  onChange: (next: Selection) => void;
}

/** A checkbox that supports the indeterminate (partial) visual state. */
function TriStateCheckbox({
  checked,
  indeterminate,
  onChange,
  "aria-label": ariaLabel,
}: {
  checked: boolean;
  indeterminate?: boolean;
  onChange: () => void;
  "aria-label"?: string;
}) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = Boolean(indeterminate) && !checked;
  }, [indeterminate, checked]);
  return (
    <input
      ref={ref}
      type="checkbox"
      className="h-4 w-4 shrink-0"
      checked={checked}
      onChange={onChange}
      aria-label={ariaLabel}
    />
  );
}

/** A searchable, checkbox-driven picker over grouped resources. Controlled: the
 * parent owns the {@link Selection} and receives every change. */
export function ResourceSelector({ groups, selection, onChange }: ResourceSelectorProps) {
  const [query, setQuery] = useState("");
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  const needle = query.trim().toLowerCase();
  const filtered = useMemo(() => {
    if (!needle) return groups;
    return groups
      .map((g) => ({
        ...g,
        items: g.items.filter(
          (i) =>
            i.label.toLowerCase().includes(needle) ||
            (i.sublabel ?? "").toLowerCase().includes(needle),
        ),
      }))
      .filter((g) => g.items.length > 0);
  }, [groups, needle]);

  function isSelected(type: string, id: string): boolean {
    return (selection[type] ?? []).includes(id);
  }

  function toggleItem(type: string, id: string) {
    const current = selection[type] ?? [];
    const next = current.includes(id) ? current.filter((x) => x !== id) : [...current, id];
    onChange({ ...selection, [type]: next });
  }

  /** Select or clear every *currently visible* item in a group. */
  function toggleGroup(group: SelectableGroup, visible: SelectableItem[]) {
    const current = new Set(selection[group.type] ?? []);
    const allVisibleSelected = visible.every((i) => current.has(i.id));
    if (allVisibleSelected) {
      visible.forEach((i) => current.delete(i.id));
    } else {
      visible.forEach((i) => current.add(i.id));
    }
    onChange({ ...selection, [group.type]: [...current] });
  }

  function setAll(select: boolean) {
    const next: Selection = { ...selection };
    for (const g of groups) {
      next[g.type] = select ? g.items.map((i) => i.id) : [];
    }
    onChange(next);
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search objects…"
          className="flex-1 rounded-md border bg-transparent px-3 py-1.5 text-sm"
        />
        <button
          type="button"
          onClick={() => setAll(true)}
          className="rounded-md border px-2.5 py-1.5 text-xs font-medium hover:bg-accent"
        >
          Select all
        </button>
        <button
          type="button"
          onClick={() => setAll(false)}
          className="rounded-md border px-2.5 py-1.5 text-xs font-medium hover:bg-accent"
        >
          Clear all
        </button>
      </div>

      {filtered.length === 0 ? (
        <p className="py-6 text-center text-sm text-muted-foreground">No matching objects.</p>
      ) : (
        <div className="divide-y rounded-md border">
          {filtered.map((group) => {
            const selectedCount = (selection[group.type] ?? []).length;
            const total = group.items.length;
            const allVisibleSelected = group.items.every((i) => isSelected(group.type, i.id));
            const isCollapsed = collapsed[group.type] ?? false;
            return (
              <div key={group.type}>
                <div className="flex items-center gap-2 px-3 py-2">
                  <TriStateCheckbox
                    checked={allVisibleSelected}
                    indeterminate={selectedCount > 0}
                    onChange={() => toggleGroup(group, group.items)}
                    aria-label={`Select all ${group.label}`}
                  />
                  <button
                    type="button"
                    onClick={() => setCollapsed((c) => ({ ...c, [group.type]: !isCollapsed }))}
                    className="flex flex-1 items-center gap-1 text-left text-sm font-medium"
                  >
                    {isCollapsed ? (
                      <ChevronRight className="h-4 w-4 text-muted-foreground" />
                    ) : (
                      <ChevronDown className="h-4 w-4 text-muted-foreground" />
                    )}
                    {group.label}
                    <span className="ml-1 text-xs font-normal text-muted-foreground">
                      {selectedCount}/{total}
                    </span>
                  </button>
                </div>
                {!isCollapsed ? (
                  <ul className="space-y-0.5 px-3 pb-2 pl-9">
                    {group.items.map((item) => (
                      <li key={item.id}>
                        <label className="flex cursor-pointer items-center gap-2 rounded px-1 py-1 text-sm hover:bg-accent/50">
                          <TriStateCheckbox
                            checked={isSelected(group.type, item.id)}
                            onChange={() => toggleItem(group.type, item.id)}
                            aria-label={item.label}
                          />
                          <span className="truncate">{item.label}</span>
                          {item.sublabel ? (
                            <span className="truncate text-xs text-muted-foreground">{item.sublabel}</span>
                          ) : null}
                        </label>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
