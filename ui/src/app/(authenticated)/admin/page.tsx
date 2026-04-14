"use client";

import { useState } from "react";

import { DimensionManager } from "@/components/admin/DimensionManager";
import { cn } from "@/lib/utils";
import type { DimensionKind } from "@/lib/api/dimensions";

interface TabDef {
  kind: DimensionKind;
  label: string;
}

const TABS: TabDef[] = [
  { kind: "regions", label: "Regions" },
  { kind: "departments", label: "Departments" },
  { kind: "roles", label: "Roles" },
  { kind: "groups", label: "Groups" },
];

export default function AdminPage() {
  const [active, setActive] = useState<DimensionKind>("regions");

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Admin</h1>
        <p className="text-sm text-muted-foreground">
          Manage permission dimensions for your organization.
        </p>
      </div>

      <div className="flex gap-1 border-b" role="tablist">
        {TABS.map((tab) => (
          <button
            key={tab.kind}
            type="button"
            role="tab"
            aria-selected={active === tab.kind}
            onClick={() => setActive(tab.kind)}
            className={cn(
              "border-b-2 px-4 py-2 text-sm font-medium transition-colors",
              active === tab.kind
                ? "border-primary text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <DimensionManager key={active} kind={active} label={TABS.find((t) => t.kind === active)!.label} />
    </div>
  );
}
