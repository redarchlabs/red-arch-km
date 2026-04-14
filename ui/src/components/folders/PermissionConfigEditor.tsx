"use client";

import { Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  type Dimension,
  type DimensionKind,
  listDimensions,
} from "@/lib/api/dimensions";
import { cn } from "@/lib/utils";

export interface PermissionEntry {
  region?: string;
  department?: string;
  role?: string;
  group?: string;
}

interface PermissionConfigEditorProps {
  value: PermissionEntry[];
  onChange: (next: PermissionEntry[]) => void;
  label: string;
}

const DIMENSIONS: Array<{ kind: DimensionKind; key: keyof PermissionEntry; label: string }> = [
  { kind: "regions", key: "region", label: "Region" },
  { kind: "departments", key: "department", label: "Department" },
  { kind: "roles", key: "role", label: "Role" },
  { kind: "groups", key: "group", label: "Group" },
];

export function PermissionConfigEditor({ value, onChange, label }: PermissionConfigEditorProps) {
  const [options, setOptions] = useState<Record<DimensionKind, Dimension[]>>({
    regions: [],
    departments: [],
    roles: [],
    groups: [],
  });

  useEffect(() => {
    Promise.all(DIMENSIONS.map((d) => listDimensions(d.kind)))
      .then(([regions, departments, roles, groups]) => {
        setOptions({ regions, departments, roles, groups });
      })
      .catch(() => {
        // Non-fatal: editor still renders, just with empty dropdowns.
      });
  }, []);

  const addEntry = () => onChange([...value, {}]);
  const removeEntry = (idx: number) => onChange(value.filter((_, i) => i !== idx));
  const updateEntry = (idx: number, key: keyof PermissionEntry, dimValue: string) => {
    const next = [...value];
    const current = { ...next[idx] };
    if (dimValue) current[key] = dimValue;
    else delete current[key];
    next[idx] = current;
    onChange(next);
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <label className="text-sm font-medium">{label}</label>
        <Button type="button" variant="outline" size="sm" onClick={addEntry}>
          <Plus className="h-3 w-3" />
          Add rule
        </Button>
      </div>
      {value.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          No restrictions — anyone in the org can access.
        </p>
      ) : (
        <ul className="space-y-2">
          {value.map((entry, idx) => (
            <li key={idx} className={cn("flex flex-wrap items-center gap-2 rounded-md border p-2")}>
              {DIMENSIONS.map((d) => (
                <select
                  key={d.kind}
                  value={entry[d.key] ?? ""}
                  onChange={(e) => updateEntry(idx, d.key, e.target.value)}
                  className="h-8 rounded-md border bg-background px-2 text-sm"
                >
                  <option value="">Any {d.label.toLowerCase()}</option>
                  {options[d.kind].map((opt) => (
                    <option key={opt.id} value={opt.name}>
                      {opt.name}
                    </option>
                  ))}
                </select>
              ))}
              <Button
                type="button"
                variant="ghost"
                size="icon"
                onClick={() => removeEntry(idx)}
                aria-label="Remove rule"
                className="ml-auto"
              >
                <Trash2 className="h-4 w-4" />
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
