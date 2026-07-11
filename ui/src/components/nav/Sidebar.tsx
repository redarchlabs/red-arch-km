"use client";

import {
  BarChart3,
  Bot,
  ClipboardList,
  Database,
  FileText,
  FolderTree,
  Globe,
  LayoutDashboard,
  MessageCircle,
  Search,
  Shield,
  Workflow,
  X,
} from "lucide-react";
import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect } from "react";

import { MobileDrawer } from "@/components/ui/MobileDrawer";
import { useOrg } from "@/context/OrgContext";
import { useTheme } from "@/context/ThemeContext";
import { cn } from "@/lib/utils";

interface NavItem {
  href: string;
  label: string;
  icon: typeof FileText;
  requiresSiteAdmin?: boolean;
  requiresOrgAdmin?: boolean;
}

const NAV_ITEMS: NavItem[] = [
  { href: "/folders", label: "Resources", icon: FolderTree },
  { href: "/documents/search", label: "Search", icon: Search },
  { href: "/entities", label: "Entities", icon: Database },
  { href: "/forms", label: "Forms", icon: ClipboardList },
  { href: "/views", label: "Views", icon: LayoutDashboard },
  { href: "/reports", label: "Reports", icon: BarChart3 },
  { href: "/workflows", label: "Workflows", icon: Workflow },
  { href: "/agents", label: "Agents", icon: Bot, requiresOrgAdmin: true },
  { href: "/chat", label: "Chat", icon: MessageCircle },
  { href: "/admin", label: "Admin", icon: Shield, requiresSiteAdmin: true },
  { href: "/site-admin", label: "Site Admin", icon: Globe, requiresSiteAdmin: true },
];

interface SidebarProps {
  /** Mobile drawer open state. The desktop rail ignores it (always docked). */
  open: boolean;
  onClose: () => void;
}

export function Sidebar({ open, onClose }: SidebarProps) {
  const pathname = usePathname();
  const { isSiteAdmin, isOrgAdmin } = useOrg();
  const { theme } = useTheme();

  // Escape closes the mobile drawer (MobileDrawer also handles this, but keep
  // link-tap close local so navigation from the drawer dismisses it).
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const items = NAV_ITEMS.filter(
    (i) => (!i.requiresSiteAdmin || isSiteAdmin) && (!i.requiresOrgAdmin || isOrgAdmin),
  );
  // Highlight only the most specific matching route so that, e.g.,
  // /documents/search lights up "Search" and not also "Documents".
  const activeHref = items
    .filter((i) => pathname === i.href || pathname?.startsWith(`${i.href}/`))
    .sort((a, b) => b.href.length - a.href.length)[0]?.href;

  const body = (
    <>
      <div className="flex h-14 items-center gap-2 border-b px-4">
        {/* Original Knowledge Manager arch logo (see ui/LOGO-LICENSE.md).
            Shown only in the Red Arch theme: the asset has a baked-in cream
            background that clashes with the light/dark palettes. Pre-resized
            96px asset + unoptimized: no sharp/image-optimizer dependency. */}
        {theme === "redarch" ? (
          <Image
            src="/images/redarch-logo-small.png"
            alt="Red Arch logo"
            width={32}
            height={24}
            unoptimized
            className="h-6 w-8 rounded-sm object-cover"
          />
        ) : null}
        <span className="min-w-0 flex-1 truncate font-semibold">Red Arch Knowledge Manager</span>
        {/* Close affordance only shown in the mobile drawer. */}
        <button
          type="button"
          onClick={onClose}
          aria-label="Close navigation"
          className="rounded-sm p-1 text-muted-foreground hover:text-foreground lg:hidden"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
      <nav className="flex-1 space-y-1 p-2">
        {items.map(({ href, label, icon: Icon }) => {
          const active = href === activeHref;
          return (
            <Link
              key={href}
              href={href}
              onClick={onClose}
              className={cn(
                "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
                active
                  ? "bg-accent font-medium text-accent-foreground"
                  : "text-muted-foreground hover:bg-accent hover:text-foreground",
              )}
            >
              <Icon className="h-4 w-4" />
              {label}
            </Link>
          );
        })}
      </nav>
    </>
  );

  return (
    <>
      {/* Desktop (lg+): always-docked rail, part of the flex layout. */}
      <aside className="hidden h-full w-56 flex-col border-r bg-muted/30 lg:flex">
        {body}
      </aside>

      {/* Mobile / tablet: overlay drawer with backdrop. */}
      <div className="lg:hidden">
        <MobileDrawer
          open={open}
          onClose={onClose}
          side="left"
          label="Main navigation"
          className="w-72 max-w-[80%]"
        >
          {body}
        </MobileDrawer>
      </div>
    </>
  );
}
