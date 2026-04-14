"use client";

import { FileText, FolderTree, MessageCircle, Shield } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { useOrg } from "@/context/OrgContext";
import { cn } from "@/lib/utils";

interface NavItem {
  href: string;
  label: string;
  icon: typeof FileText;
  requiresSiteAdmin?: boolean;
}

const NAV_ITEMS: NavItem[] = [
  { href: "/documents", label: "Documents", icon: FileText },
  { href: "/folders", label: "Folders", icon: FolderTree },
  { href: "/chat", label: "Chat", icon: MessageCircle },
  { href: "/admin", label: "Admin", icon: Shield, requiresSiteAdmin: true },
];

export function Sidebar() {
  const pathname = usePathname();
  const { isSiteAdmin } = useOrg();

  return (
    <aside className="flex h-full w-56 flex-col border-r bg-muted/30">
      <div className="flex h-14 items-center border-b px-4">
        <span className="font-semibold">Red Arch KM</span>
      </div>
      <nav className="flex-1 space-y-1 p-2">
        {NAV_ITEMS.filter((i) => !i.requiresSiteAdmin || isSiteAdmin).map(
          ({ href, label, icon: Icon }) => {
            const active = pathname?.startsWith(href) ?? false;
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
          },
        )}
      </nav>
    </aside>
  );
}
