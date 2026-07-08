/**
 * Typed errors so tool handlers can turn failures into clear, actionable MCP
 * messages instead of opaque stack traces.
 */

/** The user isn't signed in (no Clerk session), so we can't mint a token. */
export class NotAuthenticatedError extends Error {
  constructor(message = "Not signed in to KM2. Run km2_login and complete the browser sign-in first.") {
    super(message);
    this.name = "NotAuthenticatedError";
  }
}

/** No active organization is selected — the exact "No organizations" / X-Org-ID-required state. */
export class NoOrgError extends Error {
  constructor(
    message = "No active organization. The signed-in account has no org selected (X-Org-ID missing). " +
      "Pick an org in the app, set KM2_ORG_ID, or call km2_set_org.",
  ) {
    super(message);
    this.name = "NoOrgError";
  }
}

/** A non-2xx response from the KM2 API. Carries the backend's own `detail`. */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;
  readonly method: string;
  readonly path: string;

  constructor(status: number, detail: unknown, method: string, path: string) {
    const detailText =
      typeof detail === "string" ? detail : detail === undefined ? "" : JSON.stringify(detail);
    super(`KM2 API ${method} ${path} → ${status}${detailText ? `: ${detailText}` : ""}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.method = method;
    this.path = path;
  }
}
