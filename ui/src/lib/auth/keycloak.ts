/**
 * Keycloak authentication integration.
 *
 * Initializes Keycloak, handles login/logout, and provides token access.
 */

import Keycloak from "keycloak-js";

let keycloakInstance: Keycloak | null = null;

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

export async function initKeycloak(): Promise<boolean> {
  const kc = getKeycloak();
  const authenticated = await kc.init({
    onLoad: "login-required",
    checkLoginIframe: false,
    pkceMethod: "S256",
  });
  return authenticated;
}

export function getToken(): string | undefined {
  return getKeycloak().token;
}

export async function refreshToken(minValidity: number = 30): Promise<boolean> {
  return getKeycloak().updateToken(minValidity);
}

export function logout(): void {
  getKeycloak().logout();
}
