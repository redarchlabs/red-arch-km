"use client";

import { LogOut, Menu } from "lucide-react";

import { HelpButton } from "@/components/help/HelpButton";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/context/AuthContext";

import { OrgSwitcher } from "./OrgSwitcher";
import { ThemeSwitcher } from "./ThemeSwitcher";

interface HeaderProps {
  /** Opens the mobile navigation drawer (hamburger, shown below lg). */
  onMenuClick: () => void;
}

export function Header({ onMenuClick }: HeaderProps) {
  const { username, logout } = useAuth();

  return (
    <header className="flex h-14 items-center justify-between gap-2 border-b px-4 sm:px-6">
      <div className="flex min-w-0 items-center gap-2">
        <button
          type="button"
          onClick={onMenuClick}
          aria-label="Open navigation"
          className="rounded-sm p-1 text-muted-foreground hover:text-foreground lg:hidden"
        >
          <Menu className="h-5 w-5" />
        </button>
        <OrgSwitcher />
      </div>
      <div className="flex items-center gap-2 sm:gap-4">
        <HelpButton />
        <ThemeSwitcher />
        <span className="hidden text-sm text-muted-foreground sm:inline">{username}</span>
        <Button variant="ghost" size="icon" onClick={logout} aria-label="Sign out">
          <LogOut className="h-4 w-4" />
        </Button>
      </div>
    </header>
  );
}
