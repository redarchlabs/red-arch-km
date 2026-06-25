"use client";

import { useAuth as useClerkAuth, useClerk, useUser } from "@clerk/nextjs";

/**
 * Auth facade over Clerk.
 *
 * `<ClerkProvider>` (in app/layout.tsx) replaces the old custom AuthProvider;
 * this hook keeps the original `useAuth()` shape so existing consumers
 * (Header, OrgContext, the authenticated-layout gate, the login page) need no
 * change. Identity now comes from Clerk's `useAuth()`/`useUser()`.
 */
interface AuthState {
  isAuthenticated: boolean;
  isInitializing: boolean;
  username: string;
  email: string;
  logout: () => void;
}

export function useAuth(): AuthState {
  const { isLoaded, isSignedIn } = useClerkAuth();
  const { user } = useUser();
  const { signOut } = useClerk();

  return {
    isAuthenticated: Boolean(isSignedIn),
    isInitializing: !isLoaded,
    username: user?.username ?? user?.firstName ?? "",
    email: user?.primaryEmailAddress?.emailAddress ?? "",
    logout: () => {
      // Clear persisted per-user state before signing out so the next login
      // doesn't inherit a stale org selection.
      if (typeof window !== "undefined") {
        localStorage.removeItem("redarch:currentOrgId");
      }
      void signOut({ redirectUrl: "/login" });
    },
  };
}
