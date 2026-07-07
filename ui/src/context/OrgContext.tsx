"use client";

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

import { fetchMe } from "@/lib/api/users";

import { useAuth } from "./AuthContext";

interface OrgSummary {
  id: string;
  name: string;
  is_admin: boolean;
}

interface OrgState {
  orgs: OrgSummary[];
  currentOrgId: string | null;
  currentOrg: OrgSummary | null;
  isSiteAdmin: boolean;
  /** True if the user administers the current org (site admins: always true). */
  isOrgAdmin: boolean;
  isLoading: boolean;
  setCurrentOrgId: (id: string) => void;
  refresh: () => Promise<void>;
}

const OrgContext = createContext<OrgState | null>(null);

const STORAGE_KEY = "redarch:currentOrgId";

export function OrgProvider({ children }: { children: ReactNode }) {
  const { isAuthenticated, isInitializing } = useAuth();
  const [orgs, setOrgs] = useState<OrgSummary[]>([]);
  const [currentOrgId, setCurrentOrgIdState] = useState<string | null>(null);
  const [isSiteAdmin, setIsSiteAdmin] = useState(false);
  const [isLoading, setIsLoading] = useState(true);

  const refresh = useCallback(async () => {
    if (!isAuthenticated) {
      setIsLoading(false);
      return;
    }
    try {
      const me = await fetchMe();
      setOrgs(me.orgs);
      setIsSiteAdmin(me.is_site_admin);

      // Hydrate current org from localStorage, fall back to first accessible org
      const stored = typeof window !== "undefined" ? localStorage.getItem(STORAGE_KEY) : null;
      const valid = stored && me.orgs.some((o: OrgSummary) => o.id === stored);
      const resolved = valid ? stored : (me.orgs[0]?.id ?? null);
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
      value={{ orgs, currentOrgId, currentOrg, isSiteAdmin, isOrgAdmin, isLoading, setCurrentOrgId, refresh }}
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
