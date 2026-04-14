"use client";

import { useState } from "react";

import { AttributeManager } from "@/components/admin/AttributeManager";
import { DimensionManager } from "@/components/admin/DimensionManager";
import { MembershipManager } from "@/components/admin/MembershipManager";
import { TagManager } from "@/components/admin/TagManager";
import type { DimensionKind } from "@/lib/api/dimensions";
import { cn } from "@/lib/utils";

type AdminTab =
  | "regions"
  | "departments"
  | "roles"
  | "groups"
  | "tags"
  | "attributes"
  | "members";

const DIMENSION_LABELS: Record<DimensionKind, string> = {
  regions: "Regions",
  departments: "Departments",
  roles: "Roles",
  groups: "Groups",
};

const TABS: ReadonlyArray<{ key: AdminTab; label: string }> = [
  { key: "regions", label: "Regions" },
  { key: "departments", label: "Departments" },
  { key: "roles", label: "Roles" },
  { key: "groups", label: "Groups" },
  { key: "tags", label: "Tags" },
  { key: "attributes", label: "Attributes" },
  { key: "members", label: "Members" },
];

function isDimension(key: AdminTab): key is DimensionKind {
  return key === "regions" || key === "departments" || key === "roles" || key === "groups";
}

export default function AdminPage() {
  const [active, setActive] = useState<AdminTab>("regions");

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Admin</h1>
        <p className="text-sm text-muted-foreground">
          Manage permissions, classification, and members for your organization.
        </p>
      </div>

      <div className="flex flex-wrap gap-1 border-b" role="tablist">
        {TABS.map((tab) => (
          <button
            key={tab.key}
            type="button"
            role="tab"
            aria-selected={active === tab.key}
            onClick={() => setActive(tab.key)}
            className={cn(
              "border-b-2 px-4 py-2 text-sm font-medium transition-colors",
              active === tab.key
                ? "border-primary text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {isDimension(active) ? (
        <DimensionManager key={active} kind={active} label={DIMENSION_LABELS[active]} />
      ) : null}
      {active === "tags" ? <TagManager /> : null}
      {active === "attributes" ? <AttributeManager /> : null}
      {active === "members" ? <MembershipManager /> : null}
    </div>
  );
}
