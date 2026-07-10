"use client";

import { BarChart3, Pencil, Plus, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { ReportBuilder } from "@/components/reports/ReportBuilder";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { getApiErrorMessage } from "@/lib/api/errors";
import { deleteReport, listReports, type Report } from "@/lib/api/reports";

type Editing = Report | "new" | null;

export default function ReportsPage() {
  const [reports, setReports] = useState<Report[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<Editing>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setReports(await listReports());
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to load reports"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const remove = async (report: Report) => {
    if (!window.confirm(`Delete report “${report.name}”?`)) return;
    try {
      await deleteReport(report.id);
      await load();
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Delete failed"));
    }
  };

  if (editing !== null) {
    return (
      <div className="space-y-4">
        <div className="flex items-center gap-2">
          <BarChart3 className="h-6 w-6" />
          <h1 className="text-2xl font-semibold">{editing === "new" ? "New report" : editing.name}</h1>
        </div>
        <ReportBuilder
          report={editing === "new" ? undefined : editing}
          onSaved={() => {
            setEditing(null);
            void load();
          }}
          onCancel={() => setEditing(null)}
        />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <BarChart3 className="h-6 w-6" />
          <h1 className="text-2xl font-semibold">Reports</h1>
        </div>
        <Button onClick={() => setEditing("new")}>
          <Plus className="h-4 w-4" />
          New report
        </Button>
      </div>

      {error ? <p className="text-sm text-destructive">{error}</p> : null}

      {loading ? (
        <Skeleton className="h-32 w-full" />
      ) : reports.length === 0 ? (
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted-foreground">
            No reports yet. Create one to chart your records — group by a field, pick metrics, and choose a
            visualization.
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {reports.map((report) => (
            <Card key={report.id}>
              <CardContent className="space-y-2 pt-6">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="truncate font-medium">{report.name}</div>
                    <div className="text-xs text-muted-foreground">{report.viz?.type ?? "bar"}</div>
                  </div>
                  <div className="flex shrink-0 gap-1">
                    <Button variant="ghost" size="icon" onClick={() => setEditing(report)} aria-label="Edit report">
                      <Pencil className="h-4 w-4" />
                    </Button>
                    <Button variant="ghost" size="icon" onClick={() => void remove(report)} aria-label="Delete report">
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                </div>
                {report.description ? (
                  <p className="line-clamp-2 text-sm text-muted-foreground">{report.description}</p>
                ) : null}
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
