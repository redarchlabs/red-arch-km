/**
 * Thin HTTP client for the KM2 backend.
 *
 * Every call harvests a fresh Clerk JWT and the active org from BrowserSession
 * and attaches them as `Authorization: Bearer …` + `X-Org-ID`, exactly matching
 * the app's axios interceptor (ui/src/lib/api/client.ts). Non-2xx responses are
 * turned into ApiError carrying the backend's `detail`.
 */
import type { BrowserSession } from "./browserSession.js";
import type { Config } from "./config.js";
import { ApiError, NoOrgError, NotAuthenticatedError } from "./errors.js";
import { logger } from "./logger.js";

export type QueryValue = string | number | boolean | undefined | null | Array<string | number>;

export interface RequestOptions {
  query?: Record<string, QueryValue>;
  body?: unknown;
  /** Explicit org override for this call; falls back to the session's active org. */
  orgId?: string;
  /** Whether an X-Org-ID is required (default true). A few endpoints (e.g. /users/me) don't need it. */
  requireOrg?: boolean;
}

export class ApiClient {
  private readonly cfg: Config;
  private readonly session: BrowserSession;

  constructor(cfg: Config, session: BrowserSession) {
    this.cfg = cfg;
    this.session = session;
  }

  private buildUrl(pathname: string, query?: Record<string, QueryValue>): string {
    const url = new URL(this.cfg.apiUrl + pathname);
    if (query) {
      for (const [key, value] of Object.entries(query)) {
        if (value === undefined || value === null) continue;
        if (Array.isArray(value)) {
          for (const v of value) url.searchParams.append(key, String(v));
        } else {
          url.searchParams.set(key, String(value));
        }
      }
    }
    return url.toString();
  }

  async request<T = unknown>(method: string, pathname: string, opts: RequestOptions = {}): Promise<T> {
    const token = await this.session.getToken();
    if (!token) throw new NotAuthenticatedError();

    const requireOrg = opts.requireOrg ?? true;
    const orgId = opts.orgId ?? (await this.session.getOrgId());
    if (requireOrg && !orgId) throw new NoOrgError();

    const headers: Record<string, string> = {
      Authorization: `Bearer ${token}`,
      Accept: "application/json",
    };
    if (orgId) headers["X-Org-ID"] = orgId;

    let payload: string | undefined;
    if (opts.body !== undefined) {
      headers["Content-Type"] = "application/json";
      payload = JSON.stringify(opts.body);
    }

    const url = this.buildUrl(pathname, opts.query);
    logger.debug(`${method} ${url}`);

    const res = await fetch(url, { method, headers, body: payload });

    if (res.status === 204 || res.status === 205) return null as T;

    const text = await res.text();
    let parsed: unknown = undefined;
    if (text) {
      try {
        parsed = JSON.parse(text);
      } catch {
        parsed = text;
      }
    }

    if (!res.ok) {
      const detail =
        parsed && typeof parsed === "object" && "detail" in (parsed as Record<string, unknown>)
          ? (parsed as Record<string, unknown>).detail
          : (parsed ?? res.statusText);
      throw new ApiError(res.status, detail, method, pathname);
    }

    return parsed as T;
  }

  get<T = unknown>(pathname: string, opts?: RequestOptions): Promise<T> {
    return this.request<T>("GET", pathname, opts);
  }
  post<T = unknown>(pathname: string, opts?: RequestOptions): Promise<T> {
    return this.request<T>("POST", pathname, opts);
  }
  patch<T = unknown>(pathname: string, opts?: RequestOptions): Promise<T> {
    return this.request<T>("PATCH", pathname, opts);
  }
  delete<T = unknown>(pathname: string, opts?: RequestOptions): Promise<T> {
    return this.request<T>("DELETE", pathname, opts);
  }
}
