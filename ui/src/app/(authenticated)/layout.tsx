"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { HelpDock } from "@/components/help/HelpDock";
import { Header } from "@/components/nav/Header";
import { Sidebar } from "@/components/nav/Sidebar";
import { useAuth } from "@/context/AuthContext";
import { HelpProvider } from "@/context/HelpContext";
import { useOrg } from "@/context/OrgContext";
import { fetchSetupStatus } from "@/lib/api/setup";

interface Props {
  children: ReactNode;
}

export default function AuthenticatedLayout({ children }: Props) {
  const router = useRouter();
  const pathname = usePathname();
  const [navOpen, setNavOpen] = useState(false);
  const { isAuthenticated, isInitializing } = useAuth();
  const { orgs, isLoading: orgLoading } = useOrg();
  const setupCheckedRef = useRef(false);

  // Close the mobile nav drawer on any route change (covers programmatic nav).
  useEffect(() => {
    setNavOpen(false);
  }, [pathname]);

  useEffect(() => {
    if (!isInitializing && !isAuthenticated) {
      router.replace("/login");
    }
  }, [isAuthenticated, isInitializing, router]);

  // First-run funnel: on an uninitialized instance (no site admin yet) any
  // signed-in orgless user is routed to the token wizard. An already-set-up
  // instance never force-redirects — an orgless site admin gets a "create
  // one" link in the org switcher instead of being trapped in /setup on
  // every navigation. Checked once per mount to avoid hammering the API.
  useEffect(() => {
    if (isInitializing || !isAuthenticated || orgLoading) return;
    if (orgs.length > 0 || setupCheckedRef.current) return;
    setupCheckedRef.current = true;
    void (async () => {
      try {
        const status = await fetchSetupStatus();
        if (status.needs_setup) {
          router.replace("/setup");
        }
      } catch {
        // Status check is best-effort; the app shell still renders.
      }
    })();
  }, [isInitializing, isAuthenticated, orgLoading, orgs.length, router]);

  if (isInitializing) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <p className="text-sm text-muted-foreground">Loading…</p>
      </div>
    );
  }

  if (!isAuthenticated) {
    return null;
  }

  return (
    <HelpProvider>
      <div className="flex h-screen">
        <Sidebar open={navOpen} onClose={() => setNavOpen(false)} />
        <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
          <Header onMenuClick={() => setNavOpen(true)} />
          <main className="flex-1 overflow-auto p-4 sm:p-6">{children}</main>
        </div>
        <HelpDock />
      </div>
    </HelpProvider>
  );
}
