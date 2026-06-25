"use client";

import { SignIn } from "@clerk/nextjs";

/**
 * Sign-in page. Renders Clerk's <SignIn/> (email+password + Google per D5).
 * Hash routing keeps the multi-step flow on /login without a catch-all route.
 * An already-signed-in user is redirected to /documents by Clerk.
 */
export default function LoginPage() {
  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <SignIn routing="hash" signUpUrl="/sign-up" fallbackRedirectUrl="/documents" />
    </div>
  );
}
