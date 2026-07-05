import type { ReactNode } from "react";

/**
 * Minimal centered shell for the first-run wizard — deliberately outside the
 * (authenticated) group so the Sidebar/OrgSwitcher chrome (which assumes an
 * org exists) never renders here. Clerk middleware still requires sign-in.
 */
export default function SetupLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-muted/30 p-4">
      <div className="w-full max-w-lg">{children}</div>
    </div>
  );
}
