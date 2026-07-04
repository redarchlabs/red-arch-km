"use client";

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

import { fetchMe } from "@/lib/api/users";

import { useAuth } from "./AuthContext";

interface OrgSummary {
  id: string;
  name: string;
}

interface OrgState {
  orgs: OrgSummary[];
  currentOrgId: string | null;
  currentOrg: OrgSummary | null;
  isSiteAdmin: boolean;
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
      setCurrentOrgIdState(valid ? stored : me.orgs[0]?.id ?? null);
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

  return (
    <OrgContext.Provider
      value={{ orgs, currentOrgId, currentOrg, isSiteAdmin, isLoading, setCurrentOrgId, refresh }}
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
