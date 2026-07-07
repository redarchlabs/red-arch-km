import type { ReactNode } from "react";

import { PublicHeader } from "@/components/landing/PublicHeader";

/**
 * Public shell for the ungated /help marketing pages. No auth: `/help(.*)` is
 * listed in middleware's isPublicRoute so logged-out visitors can read it.
 */
export default function PublicHelpLayout({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen bg-background text-foreground">
      <PublicHeader />
      <main>{children}</main>
    </div>
  );
}
