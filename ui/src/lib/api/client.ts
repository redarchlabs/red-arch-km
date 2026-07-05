/**
 * Type-safe API client with Clerk token injection and org-scoping.
 */

import axios, { type AxiosInstance } from "axios";

import { getToken } from "@/lib/auth/clerk";

const STORAGE_KEY = "redarch:currentOrgId";

const apiClient: AxiosInstance = axios.create({
  baseURL: process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api",
  timeout: 30000,
  headers: {
    "Content-Type": "application/json",
  },
});

apiClient.interceptors.request.use(async (config) => {
  // Clerk's getToken() transparently refreshes the short-lived session JWT.
  // Cross-origin (:3000 → :8000), so the Bearer header is required.
  const token = await getToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }

  // Attach org scope for endpoints that require it. A per-request X-Org-ID
  // (set by the site-admin console to operate on a specific org) wins over
  // the ambient org from localStorage. `has()` is case-insensitive, so any
  // caller-supplied casing of the header is respected.
  if (typeof window !== "undefined" && !config.headers.has("X-Org-ID")) {
    const orgId = localStorage.getItem(STORAGE_KEY);
    if (orgId) {
      config.headers["X-Org-ID"] = orgId;
    }
  }

  return config;
});

// Single-flight flag: prevents a storm of concurrent 401s from triggering
// multiple redirects. Only the first 401 wins; subsequent ones just reject.
// The flag is reset after a short window so that if the navigation is
// cancelled (e.g. a beforeunload confirm) subsequent 401s can still redirect
// instead of silently rejecting forever.
let isRedirectingToLogin = false;

apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (
      error.response?.status === 401 &&
      typeof window !== "undefined" &&
      !isRedirectingToLogin &&
      !window.location.pathname.startsWith("/login")
    ) {
      isRedirectingToLogin = true;
      window.location.href = "/login";
      // If the navigation was cancelled, reset the flag so future 401s can
      // retry the redirect. In the normal path the page unloads before the
      // timer fires and this is a no-op.
      setTimeout(() => {
        isRedirectingToLogin = false;
      }, 5000);
    }
    return Promise.reject(error);
  },
);

export default apiClient;
