"use client";

import { ChangeManagementConsole } from "@/components/change-management/ChangeManagementConsole";

export default function ChangeManagementPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Releases</h1>
        <p className="text-sm text-muted-foreground">
          Cut a frozen release of your configuration, review and approve it, then promote it to another
          environment — and roll it back if needed.
        </p>
      </div>
      <ChangeManagementConsole />
    </div>
  );
}
