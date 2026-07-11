"use client";

import { useState } from "react";

import { ExportPanel } from "@/components/import-export/ExportPanel";
import { ImportPanel } from "@/components/import-export/ImportPanel";
import { Skeleton } from "@/components/ui/skeleton";
import { useOrg } from "@/context/OrgContext";
import { cn } from "@/lib/utils";

type Tab = "export" | "import";

const TABS: ReadonlyArray<{ key: Tab; label: string }> = [
  { key: "export", label: "Export" },
  { key: "import", label: "Import" },
];

/**
 * The import/export surface: export the whole org to a portable JSON bundle, or
 * import one back in. Org-admin gated. Rendered both on its own route and as a
 * tab inside the Admin console.
 */
export function ImportExportConsole() {
  const { isOrgAdmin, isLoading } = useOrg();
  const [active, setActive] = useState<Tab>("export");

  if (isLoading) {
    return <Skeleton className="h-64 w-full" />;
  }

  if (!isOrgAdmin) {
    return <p className="text-sm text-destructive">Organization admin access required.</p>;
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        Migrate your configuration and data between organizations: export the whole org to a portable
        JSON bundle, then import it into another installation.
      </p>

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

      {active === "export" ? <ExportPanel /> : <ImportPanel />}
    </div>
  );
}
