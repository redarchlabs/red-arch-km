"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import { getKeycloak, initKeycloak, logout as kcLogout } from "@/lib/auth/keycloak";

interface AuthState {
  isAuthenticated: boolean;
  isInitializing: boolean;
  username: string;
  email: string;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [isInitializing, setIsInitializing] = useState(true);
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");

  useEffect(() => {
    initKeycloak()
      .then((authenticated) => {
        setIsAuthenticated(authenticated);
        if (authenticated) {
          const kc = getKeycloak();
          const profile = kc.tokenParsed as { preferred_username?: string; email?: string } | undefined;
          setUsername(profile?.preferred_username ?? "");
          setEmail(profile?.email ?? "");
        }
      })
      .catch(() => setIsAuthenticated(false))
      .finally(() => setIsInitializing(false));
  }, []);

  return (
    <AuthContext.Provider
      value={{ isAuthenticated, isInitializing, username, email, logout: kcLogout }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (ctx === null) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return ctx;
}
