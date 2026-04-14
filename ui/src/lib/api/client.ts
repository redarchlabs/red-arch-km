/**
 * Type-safe API client with Keycloak token injection and org-scoping.
 */

import axios, { type AxiosInstance } from "axios";

import { getToken, refreshToken } from "@/lib/auth/keycloak";

const STORAGE_KEY = "redarch:currentOrgId";

const apiClient: AxiosInstance = axios.create({
  baseURL: process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api",
  timeout: 30000,
  headers: {
    "Content-Type": "application/json",
  },
});

apiClient.interceptors.request.use(async (config) => {
  try {
    await refreshToken(30);
    const token = getToken();
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
  } catch {
    // Token refresh failed — request will proceed without auth.
    // The API will return 401 and the response interceptor will redirect.
  }

  // Attach org scope for endpoints that require it
  if (typeof window !== "undefined") {
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
