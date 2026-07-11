"use client";

import { ImportExportConsole } from "@/components/import-export/ImportExportConsole";

export default function ImportExportPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Import / Export</h1>
      </div>
      <ImportExportConsole />
    </div>
  );
}
