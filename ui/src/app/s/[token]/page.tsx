"use client";

import { Suspense, use } from "react";

import { ViewRuntime } from "@/components/views/ViewRuntime";

/**
 * A shared view, open to anyone holding the link — no sign-in.
 *
 * This route lives OUTSIDE the `(authenticated)` segment, so no app chrome, no
 * auth guard and no org context are involved: the token in the path is the only
 * credential, and everything it permits is decided server-side (the record is
 * pinned to the link, and only workflows this view's own element tree references
 * can be run). Sharing is off for every view until an org admin turns it on.
 */
export default function SharedViewPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = use(params);
  return (
    <main className="min-h-screen w-full bg-background">
      {/* ViewRuntime reads search params, which needs a boundary on a route that
          isn't already client-rendered by an auth guard. */}
      <Suspense fallback={null}>
        <ViewRuntime id="" token={token} />
      </Suspense>
    </main>
  );
}
