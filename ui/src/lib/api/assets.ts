import apiClient from "./client";

/**
 * Org assets — binary content (3D models, textures) stored per org rather than
 * shipped in the build.
 *
 * A view's config names an asset once, as `/api/assets/<path>`. Where that has
 * to be fetched from depends on who is looking:
 *
 * - signed in, it is the org-scoped route and needs the session's auth and
 *   `X-Org-ID` headers. A bare `fetch()` carries neither, and on the UI origin
 *   it would not even reach the API — so these go through the axios client.
 * - behind a share link, there is no session at all. The same asset is served
 *   by the public route keyed on the token, which must NOT carry org headers,
 *   and which the server will only answer for paths under `public/`.
 *
 * `resolveAssetUrl` picks between them, so the config stays context-free.
 * Anything else — a static path, an absolute http(s) URL — is left alone.
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

const ASSET_PREFIX = "/api/assets/";

/**
 * Rewrite an org asset URL for the context it is being rendered in.
 *
 * Only `/api/assets/...` is rewritten: a static path or an external URL means
 * exactly what it says in both contexts.
 */
export function resolveAssetUrl(url: string, shareToken?: string | null): string {
  if (!shareToken || !url.startsWith(ASSET_PREFIX)) return url;
  const path = url.slice(ASSET_PREFIX.length);
  // Absolute, because a shared page still runs on the UI origin and the public
  // route lives on the API — the same reason the branded logo is built this way.
  return `${API_BASE}/public/views/${encodeURIComponent(shareToken)}/assets/${path}`;
}

/** Does this URL need the authenticated client? */
export function isApiAsset(url: string): boolean {
  if (!url.startsWith("/api/")) return false;
  // The public share route carries its own credential and has no session behind
  // it; sending org headers there would be wrong, not merely unnecessary.
  return !url.startsWith("/api/public/");
}

/** Fetch an org asset's bytes with the session's headers attached. */
export async function fetchAssetBytes(url: string): Promise<ArrayBuffer> {
  // The axios client is configured with the API base, so strip the `/api`
  // prefix the caller wrote and let it rebuild the absolute URL.
  const path = url.replace(/^\/api/, "");
  const response = await apiClient.get<ArrayBuffer>(path, { responseType: "arraybuffer" });
  return response.data;
}
