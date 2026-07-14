"use client";

import { useEffect, useState } from "react";

import { CeleryMonitor } from "@/components/site-admin/CeleryMonitor";
import { DeploymentLogManager } from "@/components/site-admin/DeploymentLogManager";
import { GlobalMembershipManager } from "@/components/site-admin/GlobalMembershipManager";
import { OrgManager } from "@/components/site-admin/OrgManager";
import { SentEmailsManager } from "@/components/site-admin/SentEmailsManager";
import { SystemStatus } from "@/components/site-admin/SystemStatus";
import { UserManager } from "@/components/site-admin/UserManager";
import { Skeleton } from "@/components/ui/skeleton";
import { useOrg } from "@/context/OrgContext";
import { SITE_ADMIN_TAB_HELP } from "@/lib/adminHelp";
import { useHelpOverride } from "@/context/HelpContext";
import { cn } from "@/lib/utils";

type SiteAdminTab = "orgs" | "users" | "memberships" | "system" | "celery" | "emails" | "deployments";

const TABS: ReadonlyArray<{ key: SiteAdminTab; label: string }> = [
  { key: "orgs", label: "Organizations" },
  { key: "users", label: "Users" },
  { key: "memberships", label: "Memberships" },
  { key: "system", label: "System" },
  { key: "celery", label: "Celery" },
  { key: "emails", label: "Sent Emails" },
  { key: "deployments", label: "Deployments" },
];

export default function SiteAdminPage() {
  const { isSiteAdmin, isLoading } = useOrg();
  const [active, setActive] = useState<SiteAdminTab>("orgs");

  // Show help for the active tab (clears when leaving the page). Declared before
  // the early returns below to keep hook order stable.
  const setHelp = useHelpOverride();
  useEffect(() => {
    setHelp(SITE_ADMIN_TAB_HELP[active]);
    return () => setHelp(null);
  }, [active, setHelp]);

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
      {active === "celery" ? <CeleryMonitor /> : null}
      {active === "emails" ? <SentEmailsManager /> : null}
      {active === "deployments" ? <DeploymentLogManager /> : null}
    </div>
  );
}
