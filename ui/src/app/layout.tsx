import type { Metadata } from "next";
import type { ReactNode } from "react";

import { AuthProvider } from "@/context/AuthContext";
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
    <html lang="en">
      <body>
        <AuthProvider>
          <OrgProvider>{children}</OrgProvider>
        </AuthProvider>
      </body>
    </html>
  );
}
