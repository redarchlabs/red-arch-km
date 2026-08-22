"use client";

import { SignUp } from "@clerk/nextjs";
import Link from "next/link";

import { isBypassEnabled } from "@/lib/auth/bypass";

/**
 * Sign-up page (D5: in-app registration moves from Keycloak-hosted to Clerk).
 * Hash routing keeps the multi-step flow on /sign-up without a catch-all route.
 * The brand frame / centering is provided by (auth)/layout.tsx.
 *
 * Registration is inherently online — it creates an account with the identity provider —
 * so offline bypass says so plainly rather than rendering a widget that cannot work.
 */
export default function SignUpPage() {
  if (isBypassEnabled()) {
    return (
      <div className="w-full max-w-sm space-y-3">
        <h2 className="text-xl font-semibold tracking-tight">Registration unavailable</h2>
        <p className="text-sm text-muted-foreground">
          This build is running offline, so new accounts cannot be created — that needs the
          identity provider.
        </p>
        <Link href="/login" className="text-sm underline underline-offset-4">
          Back to sign-in →
        </Link>
      </div>
    );
  }
  return <SignUp routing="hash" signInUrl="/login" fallbackRedirectUrl="/documents" />;
}
