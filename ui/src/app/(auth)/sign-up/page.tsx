"use client";

import { SignUp } from "@clerk/nextjs";

/**
 * Sign-up page (D5: in-app registration moves from Keycloak-hosted to Clerk).
 * Hash routing keeps the multi-step flow on /sign-up without a catch-all route.
 * The brand frame / centering is provided by (auth)/layout.tsx.
 */
export default function SignUpPage() {
  return <SignUp routing="hash" signInUrl="/login" fallbackRedirectUrl="/documents" />;
}
