import { ClerkProvider } from "@clerk/nextjs";
import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import { ThemedToaster } from "@/components/ThemedToaster";
import { OrgProvider } from "@/context/OrgContext";
import { ThemeProvider } from "@/context/ThemeContext";
import { isBypassEnabled } from "@/lib/auth/bypass";

import "./globals.css";

export const metadata: Metadata = {
  title: "Red Arch Knowledge Management",
  description: "AI-powered enterprise knowledge management platform",
};

// Explicit viewport so mobile intent is self-documented. Never disables zoom
// (no maximumScale/userScalable) — that would be an accessibility regression.
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
};

// Stamps data-theme on <html> BEFORE first paint so a stored dark/redarch
// preference doesn't flash the light default. Must stay in sync with
// src/lib/theme.ts (THEMES / storage key).
const THEME_INIT_SCRIPT = `(function(){try{var t=localStorage.getItem("redarch:theme");if(t!=="light"&&t!=="dark"&&t!=="redarch"&&t!=="console"){t=window.matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light";}document.documentElement.dataset.theme=t;}catch(e){}})();`;

interface RootLayoutProps {
  children: ReactNode;
}

export default function RootLayout({ children }: RootLayoutProps) {
  const shell = (
    /* suppressHydrationWarning: the inline script sets data-theme before
       hydration, so the server-rendered <html> attributes never match. */
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body>
        <ThemeProvider>
          {/* OrgProvider reads auth via useAuth(), so it must nest inside ClerkProvider
              whenever Clerk is the provider. */}
          <OrgProvider>{children}</OrgProvider>
          {/* Global toast portal, theme-aware (see ThemedToaster). */}
          <ThemedToaster />
        </ThemeProvider>
      </body>
    </html>
  );

  // Offline bypass must not MOUNT ClerkProvider, not merely ignore it: this is the ROOT
  // layout, so its script fetch to clerk.accounts.dev would fire on every page — the
  // anonymous /s/<token> kiosk included — and with no internet that is a hang, not a
  // no-op. useAuth() has already been swapped to the Clerk-free implementation.
  if (isBypassEnabled()) {
    return shell;
  }

  return (
    <ClerkProvider signInUrl="/login" signUpUrl="/sign-up" afterSignOutUrl="/login">
      {shell}
    </ClerkProvider>
  );
}
