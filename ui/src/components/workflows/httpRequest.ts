/**
 * Pure form <-> node-data logic for the `http_request` connector task, mirroring
 * the backend authority `HttpRequest` in
 * `services/api/src/api/services/workflow/actions.py`.
 *
 * The connector reads its request from `data.config`:
 *   - `connection` — the connection NAME (the engine resolves it via
 *     `get_by_name`), whose stored auth + base_url are injected at run time.
 *   - `method`     — HTTP verb (default GET).
 *   - `path`       — appended to the connection's base_url, OR
 *   - `url`        — a literal absolute URL that overrides base_url + path.
 *   - `headers`    — a string→string map merged with the connection's auth header.
 *   - `body`       — a JSON value sent as the request body.
 * The response is captured into a run variable named by the TOP-LEVEL
 * `data.capture` (NOT under config) — matching the engine's `node.data["capture"]`.
 *
 * These functions never mutate their input; the inspector wires them to the
 * designer store's node-data update path.
 */

export const HTTP_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"] as const;
export type HttpMethod = (typeof HTTP_METHODS)[number];

/** Scalar string fields — connection/method/path/url live in config; capture is top-level. */
export type HttpScalarField = "connection" | "method" | "path" | "url" | "capture";
/** Object fields edited as key→value maps, both under config. */
export type HttpObjectField = "headers" | "body";

export interface HttpRequestForm {
  /** Connection NAME (resolved by name at run time), or "" for a literal url. */
  connection: string;
  method: HttpMethod;
  path: string;
  url: string;
  /** Run-variable name to store the response under; "" = don't capture. */
  capture: string;
}

function asConfig(data: Record<string, unknown>): Record<string, unknown> {
  const config = data.config;
  return config && typeof config === "object" && !Array.isArray(config)
    ? (config as Record<string, unknown>)
    : {};
}

function coerceMethod(value: unknown): HttpMethod {
  const upper = String(value ?? "").toUpperCase();
  return (HTTP_METHODS as readonly string[]).includes(upper) ? (upper as HttpMethod) : "GET";
}

/** Read a node's data into the connector form model, applying defaults. */
export function readHttpRequest(data: Record<string, unknown>): HttpRequestForm {
  const config = asConfig(data);
  return {
    connection: String(config.connection ?? ""),
    method: coerceMethod(config.method),
    path: String(config.path ?? ""),
    url: String(config.url ?? ""),
    capture: String(data.capture ?? ""),
  };
}

/** Read the current headers/body object for the reusable key→value editor. */
export function readHttpObject(data: Record<string, unknown>, field: HttpObjectField): unknown {
  return asConfig(data)[field];
}

/**
 * Return a NEW node-data object with one scalar connector field applied. `method`
 * is always persisted (it comes from a fixed select); the other config fields and
 * the top-level `capture` are PRUNED when blank so an emptied field never lingers
 * as `""`. The input object is never mutated.
 */
export function applyHttpField(
  data: Record<string, unknown>,
  field: HttpScalarField,
  value: string,
): Record<string, unknown> {
  const trimmed = value.trim();

  if (field === "capture") {
    const next = { ...data };
    if (trimmed === "") delete next.capture;
    else next.capture = trimmed;
    return next;
  }

  const config = { ...asConfig(data) };
  if (field === "method") {
    config.method = coerceMethod(value);
  } else if (trimmed === "") {
    delete config[field];
  } else {
    config[field] = trimmed;
  }
  return { ...data, config };
}

/**
 * Return a NEW node-data object with a headers/body object applied under config.
 * An empty object REMOVES the key so config stays minimal. Never mutates input.
 */
export function applyHttpObjectField(
  data: Record<string, unknown>,
  field: HttpObjectField,
  value: Record<string, unknown>,
): Record<string, unknown> {
  const config = { ...asConfig(data) };
  if (value && Object.keys(value).length > 0) {
    config[field] = value;
  } else {
    delete config[field];
  }
  return { ...data, config };
}
