"use client";

import { ChevronDown } from "lucide-react";
import { useState } from "react";

import { useOrg } from "@/context/OrgContext";
import { cn } from "@/lib/utils";

export function OrgSwitcher() {
  const { orgs, currentOrg, setCurrentOrgId } = useOrg();
  const [open, setOpen] = useState(false);

  if (orgs.length === 0) {
    return <div className="text-sm text-muted-foreground">No organizations</div>;
  }

  if (orgs.length === 1) {
    return (
      <div className="rounded-md border px-3 py-1.5 text-sm font-medium">
        {currentOrg?.name ?? orgs[0]?.name}
      </div>
    );
  }

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="inline-flex items-center gap-2 rounded-md border px-3 py-1.5 text-sm font-medium hover:bg-accent"
      >
        <span>{currentOrg?.name ?? "Select org"}</span>
        <ChevronDown className="h-4 w-4 opacity-60" />
      </button>
      {open ? (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} aria-hidden />
          <div className="absolute right-0 z-20 mt-1 w-56 overflow-hidden rounded-md border bg-background shadow-md">
            {orgs.map((org) => (
              <button
                key={org.id}
                type="button"
                onClick={() => {
                  setCurrentOrgId(org.id);
                  setOpen(false);
                }}
                className={cn(
                  "block w-full px-3 py-2 text-left text-sm hover:bg-accent",
                  org.id === currentOrg?.id && "bg-accent font-medium",
                )}
              >
                {org.name}
              </button>
            ))}
          </div>
        </>
      ) : null}
    </div>
  );
}
