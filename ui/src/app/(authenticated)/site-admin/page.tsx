"use client";

import { useState } from "react";

import { GlobalMembershipManager } from "@/components/site-admin/GlobalMembershipManager";
import { OrgManager } from "@/components/site-admin/OrgManager";
import { SystemStatus } from "@/components/site-admin/SystemStatus";
import { UserManager } from "@/components/site-admin/UserManager";
import { Skeleton } from "@/components/ui/skeleton";
import { useOrg } from "@/context/OrgContext";
import { cn } from "@/lib/utils";

type SiteAdminTab = "orgs" | "users" | "memberships" | "system";

const TABS: ReadonlyArray<{ key: SiteAdminTab; label: string }> = [
  { key: "orgs", label: "Organizations" },
  { key: "users", label: "Users" },
  { key: "memberships", label: "Memberships" },
  { key: "system", label: "System" },
];

export default function SiteAdminPage() {
  const { isSiteAdmin, isLoading } = useOrg();
  const [active, setActive] = useState<SiteAdminTab>("orgs");

  if (isLoading) {
    return <Skeleton className="h-64 w-full" />;
  }

  if (!isSiteAdmin) {
    return (
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold">Site Admin</h1>
        <p className="text-sm text-destructive">Site admin access required.</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Site Admin</h1>
        <p className="text-sm text-muted-foreground">
          Global administration: organizations, users, and platform health.
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

      {active === "orgs" ? <OrgManager /> : null}
      {active === "users" ? <UserManager /> : null}
      {active === "memberships" ? <GlobalMembershipManager /> : null}
      {active === "system" ? <SystemStatus /> : null}
    </div>
  );
}
