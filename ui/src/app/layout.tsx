import { ClerkProvider } from "@clerk/nextjs";
import type { Metadata } from "next";
import type { ReactNode } from "react";

import { OrgProvider } from "@/context/OrgContext";

import "./globals.css";

export const metadata: Metadata = {
  title: "Red Arch Knowledge Management",
  description: "AI-powered enterprise knowledge management platform",
};

interface RootLayoutProps {
  children: ReactNode;
}

export default function RootLayout({ children }: RootLayoutProps) {
  return (
    <ClerkProvider signInUrl="/login" signUpUrl="/sign-up" afterSignOutUrl="/login">
      <html lang="en">
        <body>
          {/* OrgProvider reads Clerk auth via useAuth(), so it must nest inside ClerkProvider. */}
          <OrgProvider>{children}</OrgProvider>
        </body>
      </html>
    </ClerkProvider>
  );
}
