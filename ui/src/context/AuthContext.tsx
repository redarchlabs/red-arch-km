"use client";

import { useAuth as useClerkAuth, useClerk, useUser } from "@clerk/nextjs";
import { useEffect, useState } from "react";

import {
  BYPASS_USER,
  clearBypassSecret,
  getBypassSecret,
  isBypassEnabled,
} from "@/lib/auth/bypass";

/**
 * Auth facade over the identity provider.
 *
 * `<ClerkProvider>` (in app/layout.tsx) replaces the old custom AuthProvider; this hook
 * keeps the original `useAuth()` shape so existing consumers (Header, OrgContext, the
 * authenticated-layout gate, the login page) need no change. Identity normally comes from
 * Clerk's `useAuth()`/`useUser()`.
 *
 * It also earns its keep in OFFLINE BYPASS mode (see lib/auth/bypass.ts): the whole app
 * runs with no Clerk at all, and because every consumer talks to this facade rather than
 * to Clerk directly, swapping the implementation here is the entire change.
 */
interface AuthState {
  isAuthenticated: boolean;
  isInitializing: boolean;
  username: string;
  email: string;
  logout: () => void;
}

/** Clear per-user state that must not leak into the next session. */
function clearPersistedUserState(): void {
  if (typeof window !== "undefined") {
    localStorage.removeItem("redarch:currentOrgId");
  }
}

/** The normal path: identity from Clerk. */
function useClerkBackedAuth(): AuthState {
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
      clearPersistedUserState();
      void signOut({ redirectUrl: "/login" });
    },
  };
}

/**
 * Offline bypass: signed in iff a secret has been entered in this tab.
 *
 * The secret is read in an effect rather than during render even though
 * `sessionStorage` is synchronous. Server rendering has no storage, so reading inline
 * would emit unauthenticated markup and then hydrate as authenticated — a mismatch, and a
 * visible bounce through /login. Holding `isInitializing` until after mount mirrors
 * Clerk's own `isLoaded` contract, which OrgContext already waits on before calling
 * /users/me.
 */
function useBypassAuth(): AuthState {
  const [secret, setSecret] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    setSecret(getBypassSecret());
    setReady(true);
  }, []);

  return {
    isAuthenticated: Boolean(secret),
    isInitializing: !ready,
    username: BYPASS_USER,
    email: "",
    logout: () => {
      clearPersistedUserState();
      clearBypassSecret();
      if (typeof window !== "undefined") {
        // A full load, not a router push: it drops all in-memory state that was
        // fetched as the previous identity.
        window.location.href = "/login";
      }
    },
  };
}

/**
 * Chosen once at module load, NOT per render.
 *
 * `isBypassEnabled()` reads a build-time constant so the answer never changes at runtime,
 * but branching *inside* the hook would still put a conditional `useClerkAuth()` call in
 * the render path and break the rules of hooks. Binding the implementation here keeps hook
 * order fixed for the lifetime of the bundle.
 */
export const useAuth: () => AuthState = isBypassEnabled() ? useBypassAuth : useClerkBackedAuth;
