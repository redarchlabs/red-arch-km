"use client";

import { SignIn } from "@clerk/nextjs";

import { BypassSignIn } from "@/components/auth/BypassSignIn";
import { isBypassEnabled } from "@/lib/auth/bypass";

/**
 * Sign-in page. Renders Clerk's <SignIn/> (email+password + Google per D5).
 * Hash routing keeps the multi-step flow on /login without a catch-all route.
 * An already-signed-in user is redirected to /documents by Clerk.
 * The brand frame / centering is provided by (auth)/layout.tsx.
 *
 * In offline bypass mode there is no Clerk to render — the operator enters a shared
 * secret instead (see lib/auth/bypass.ts). The two are separate components so the
 * Clerk widget's hooks never run in a tree with no <ClerkProvider>.
 */
export default function LoginPage() {
  if (isBypassEnabled()) {
    return <BypassSignIn />;
  }
  return <SignIn routing="hash" signUpUrl="/sign-up" fallbackRedirectUrl="/documents" />;
}
