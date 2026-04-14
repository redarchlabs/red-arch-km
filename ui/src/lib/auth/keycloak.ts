/**
 * Keycloak authentication integration.
 *
 * Initialises Keycloak, handles login/logout, and provides token access.
 * React 19 StrictMode mounts effects twice in development, which would
 * otherwise invoke `keycloak.init()` a second time and throw — so we
 * memoise the init promise and return the same resolved value.
 */

import Keycloak from "keycloak-js";

let keycloakInstance: Keycloak | null = null;
let initPromise: Promise<boolean> | null = null;

export function getKeycloak(): Keycloak {
  if (!keycloakInstance) {
    keycloakInstance = new Keycloak({
      url: process.env.NEXT_PUBLIC_KEYCLOAK_URL || "http://localhost:8080",
      realm: process.env.NEXT_PUBLIC_KEYCLOAK_REALM || "redarch",
      clientId: process.env.NEXT_PUBLIC_KEYCLOAK_CLIENT_ID || "redarch-km",
    });
  }
  return keycloakInstance;
}

export function initKeycloak(): Promise<boolean> {
  if (initPromise !== null) {
    return initPromise;
  }
  const kc = getKeycloak();
  initPromise = kc.init({
    onLoad: "login-required",
    checkLoginIframe: false,
    pkceMethod: "S256",
  });
  return initPromise;
}

export function getToken(): string | undefined {
  return getKeycloak().token;
}

export async function refreshToken(minValidity: number = 30): Promise<boolean> {
  return getKeycloak().updateToken(minValidity);
}

export function logout(): void {
  // Clear persisted per-user state before redirecting through Keycloak
  // so the next login doesn't inherit a stale org selection.
  if (typeof window !== "undefined") {
    localStorage.removeItem("redarch:currentOrgId");
  }
  getKeycloak().logout();
}
