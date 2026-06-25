"use client";

import { SignUp } from "@clerk/nextjs";

/**
 * Sign-up page (D5: in-app registration moves from Keycloak-hosted to Clerk).
 * Hash routing keeps the multi-step flow on /sign-up without a catch-all route.
 */
export default function SignUpPage() {
  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <SignUp routing="hash" signInUrl="/login" fallbackRedirectUrl="/documents" />
    </div>
  );
}
