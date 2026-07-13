"use client";

import { Home } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { useOrg } from "@/context/OrgContext";

/**
 * Org landing page. When the current org designates a home view it redirects to
 * that view's runtime viewer; otherwise it renders an explicit empty state so the
 * "Home" nav item always resolves to something meaningful.
 */
export default function HomePage() {
  const router = useRouter();
  const { currentOrg, isLoading } = useOrg();
  const homeViewId = currentOrg?.home_view_id ?? null;

  useEffect(() => {
    if (isLoading) return;
    if (homeViewId) {
      router.replace(`/views/${homeViewId}/view`);
    }
  }, [isLoading, homeViewId, router]);

  // While org state resolves — or while redirecting to the configured view —
  // show a lightweight placeholder rather than flashing the empty state.
  if (isLoading || homeViewId) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-3 text-center">
      <div className="flex h-12 w-12 items-center justify-center rounded-full bg-muted text-muted-foreground">
        <Home className="h-6 w-6" />
      </div>
      <h1 className="text-lg font-semibold">No Default View Configured</h1>
      <p className="max-w-md text-sm text-muted-foreground">
        An administrator can set this org&apos;s home view in Site Admin &rarr; org settings.
      </p>
    </div>
  );
}
