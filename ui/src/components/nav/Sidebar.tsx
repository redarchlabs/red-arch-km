"use client";

import { FileText, FolderTree, Globe, MessageCircle, Search, Shield } from "lucide-react";
import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { useOrg } from "@/context/OrgContext";
import { useTheme } from "@/context/ThemeContext";
import { cn } from "@/lib/utils";

interface NavItem {
  href: string;
  label: string;
  icon: typeof FileText;
  requiresSiteAdmin?: boolean;
}

const NAV_ITEMS: NavItem[] = [
  { href: "/documents", label: "Documents", icon: FileText },
  { href: "/documents/search", label: "Search", icon: Search },
  { href: "/folders", label: "Folders", icon: FolderTree },
  { href: "/chat", label: "Chat", icon: MessageCircle },
  { href: "/admin", label: "Admin", icon: Shield, requiresSiteAdmin: true },
  { href: "/site-admin", label: "Site Admin", icon: Globe, requiresSiteAdmin: true },
];

export function Sidebar() {
  const pathname = usePathname();
  const { isSiteAdmin } = useOrg();
  const { theme } = useTheme();

  return (
    <aside className="flex h-full w-56 flex-col border-r bg-muted/30">
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
        <span className="font-semibold">Red Arch KM</span>
      </div>
      <nav className="flex-1 space-y-1 p-2">
        {(() => {
          const items = NAV_ITEMS.filter((i) => !i.requiresSiteAdmin || isSiteAdmin);
          // Highlight only the most specific matching route so that, e.g.,
          // /documents/search lights up "Search" and not also "Documents".
          const activeHref = items
            .filter((i) => pathname === i.href || pathname?.startsWith(`${i.href}/`))
            .sort((a, b) => b.href.length - a.href.length)[0]?.href;
          return items.map(({ href, label, icon: Icon }) => {
            const active = href === activeHref;
            return (
              <Link
                key={href}
                href={href}
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
          });
        })()}
      </nav>
    </aside>
  );
}
