"use client";

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

import { getApiErrorMessage } from "@/lib/api/errors";
import { fetchMe } from "@/lib/api/users";

import { useAuth } from "./AuthContext";

interface OrgSummary {
  id: string;
  name: string;
  is_admin: boolean;
  /** Optional per-org landing view; drives the sidebar "Home" nav item. */
  home_view_id?: string | null;
  /** Branding for the chrome-free view routes (kiosk); `#rrggbb` accent and
   * whether a logo exists. A shared page can't use these — it has no session —
   * so its branding rides in the render payload instead. */
  accent_color?: string | null;
  has_logo?: boolean;
}

interface OrgState {
  orgs: OrgSummary[];
  currentOrgId: string | null;
  currentOrg: OrgSummary | null;
  isSiteAdmin: boolean;
  /** True if the user administers the current org (site admins: always true). */
  isOrgAdmin: boolean;
  isLoading: boolean;
  /** Why the last load failed, or null. Non-null means "unknown", NOT "empty":
   * consumers must not render an empty-state for a list they never received. */
  error: string | null;
  setCurrentOrgId: (id: string) => void;
  refresh: () => Promise<void>;
}

const OrgContext = createContext<OrgState | null>(null);

const STORAGE_KEY = "redarch:currentOrgId";

/** The org a deep link names, via `?org=<id>`. Read from the address bar rather
 * than a router hook so this works during the provider's first load, before any
 * page component has mounted. */
function orgFromLocation(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return new URLSearchParams(window.location.search).get("org");
  } catch {
    return null;
  }
}

export function OrgProvider({ children }: { children: ReactNode }) {
  const { isAuthenticated, isInitializing } = useAuth();
  const [orgs, setOrgs] = useState<OrgSummary[]>([]);
  const [currentOrgId, setCurrentOrgIdState] = useState<string | null>(null);
  const [isSiteAdmin, setIsSiteAdmin] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!isAuthenticated) {
      setIsLoading(false);
      return;
    }
    setError(null);
    try {
      const me = await fetchMe();
      setOrgs(me.orgs);
      setIsSiteAdmin(me.is_site_admin);

      // Resolution order: the org named by the LINK, then the one this browser
      // last used, then the first accessible one.
      //
      // The link comes first because it is the most specific statement of intent
      // available. A deep link to a view carries no org of its own, so a visitor
      // whose active org happens to be a different one used to land on a page
      // that 404s and get bounced somewhere generic — with nothing on screen
      // explaining that the link was for an org they simply weren't looking at.
      // A membership check gates it: an org the user cannot access is ignored
      // rather than honoured, because setting it would send an org header that
      // 403s every request for the rest of the session.
      const fromLink = orgFromLocation();
      const stored = typeof window !== "undefined" ? localStorage.getItem(STORAGE_KEY) : null;
      const isMember = (id: string | null) =>
        !!id && me.orgs.some((o: OrgSummary) => o.id === id);
      const resolved = isMember(fromLink)
        ? fromLink
        : isMember(stored)
          ? stored
          : (me.orgs[0]?.id ?? null);
      setCurrentOrgIdState(resolved);

      // Persist the resolved org too: the axios interceptor reads ONLY
      // localStorage, so leaving the fallback in React state alone means a
      // fresh session sends no X-Org-ID and every org-scoped request 400s
      // until the user manually picks an org.
      if (typeof window !== "undefined") {
        try {
          if (resolved) {
            localStorage.setItem(STORAGE_KEY, resolved);
          } else {
            localStorage.removeItem(STORAGE_KEY);
          }
        } catch {
          // Storage unavailable (private mode/quota) — in-memory state still
          // drives the UI; org-scoped calls may 400 until storage works.
        }
      }
    } catch (e: unknown) {
      // Swallowing this used to leave the switcher stuck on "No organizations"
      // for the rest of the session — indistinguishable from a user who really
      // has none, and with no way to retry. Keep whatever orgs we already have:
      // a failed *refresh* must not blank a working list.
      setError(getApiErrorMessage(e, "Could not load your organizations"));
    } finally {
      setIsLoading(false);
    }
  }, [isAuthenticated]);

  useEffect(() => {
    // Wait for AuthContext to finish initialising before hitting /users/me.
    // Without this gate we would fire the API call before Clerk has issued a
    // token, then immediately get a 401 and bounce to /login.
    if (isInitializing) return;

    // When the user logs out mid-session we must clear the org list so the
    // next (possibly different) user doesn't see the previous user's orgs
    // flash before refresh() completes.
    if (!isAuthenticated) {
      setOrgs([]);
      setCurrentOrgIdState(null);
      setIsSiteAdmin(false);
      setError(null);
      setIsLoading(false);
      return;
    }

    void refresh();
  }, [refresh, isInitializing, isAuthenticated]);

  const setCurrentOrgId = useCallback((id: string) => {
    setCurrentOrgIdState(id);
    if (typeof window !== "undefined") {
      localStorage.setItem(STORAGE_KEY, id);
    }
  }, []);

  const currentOrg = orgs.find((o) => o.id === currentOrgId) ?? null;
  // Site admins administer every org; otherwise defer to the current org's flag.
  const isOrgAdmin = isSiteAdmin || (currentOrg?.is_admin ?? false);

  return (
    <OrgContext.Provider
      value={{
        orgs,
        currentOrgId,
        currentOrg,
        isSiteAdmin,
        isOrgAdmin,
        isLoading,
        error,
        setCurrentOrgId,
        refresh,
      }}
    >
      {children}
    </OrgContext.Provider>
  );
}

export function useOrg(): OrgState {
  const ctx = useContext(OrgContext);
  if (ctx === null) {
    throw new Error("useOrg must be used within an OrgProvider");
  }
  return ctx;
}
