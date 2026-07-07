"use client";

import Image from "next/image";
import Link from "next/link";

import { ThemeSwitcher } from "@/components/nav/ThemeSwitcher";
import { buttonVariants } from "@/components/ui/button";

/**
 * Lightweight top bar for the public, logged-out surface (landing + /help).
 * Deliberately separate from the authenticated Header — no org switcher, no
 * user menu — just branding, theme, and the sign-in / sign-up entry points.
 */
export function PublicHeader() {
  return (
    <header className="sticky top-0 z-30 border-b border-border bg-background/90 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-4 sm:px-6">
        <Link href="/" className="flex items-center gap-2">
          <Image
            src="/images/redarch-logo-small.png"
            alt="Red Arch"
            width={28}
            height={28}
            priority
          />
          <span className="text-sm font-semibold tracking-tight">Red Arch Knowledge Manager</span>
        </Link>
        <nav className="flex items-center gap-1 sm:gap-2">
          <Link href="/help" className={buttonVariants({ variant: "ghost", size: "sm" })}>
            Features
          </Link>
          <ThemeSwitcher />
          <Link href="/login" className={buttonVariants({ variant: "ghost", size: "sm" })}>
            Sign in
          </Link>
          <Link href="/sign-up" className={buttonVariants({ size: "sm" })}>
            Get started
          </Link>
        </nav>
      </div>
    </header>
  );
}
