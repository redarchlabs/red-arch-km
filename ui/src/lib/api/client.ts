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
    }
    return Promise.reject(error);
  },
);

export default apiClient;
