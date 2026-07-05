"use client";

import { LogOut } from "lucide-react";

import { HelpButton } from "@/components/help/HelpButton";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/context/AuthContext";

import { OrgSwitcher } from "./OrgSwitcher";
import { ThemeSwitcher } from "./ThemeSwitcher";

export function Header() {
  const { username, logout } = useAuth();

  return (
    <header className="flex h-14 items-center justify-between border-b px-6">
      <OrgSwitcher />
      <div className="flex items-center gap-4">
        <HelpButton />
        <ThemeSwitcher />
        <span className="text-sm text-muted-foreground">{username}</span>
        <Button variant="ghost" size="icon" onClick={logout} aria-label="Sign out">
          <LogOut className="h-4 w-4" />
        </Button>
      </div>
    </header>
  );
}
