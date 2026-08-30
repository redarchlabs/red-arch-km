/**
 * Where to send someone after they sign in.
 *
 * The sign-in gate replaces whatever page was requested, so without this a deep
 * link — a station on a wall display, a shared view, a record someone was sent —
 * is lost the moment authentication is required, and the operator lands on a
 * generic landing page wondering where their link went.
 *
 * A return-to value is attacker-supplied in the general case, because it arrives
 * inside a URL that can be sent to someone. So this is an allow-list, not a
 * deny-list: the result must be a path on THIS origin, and anything that could
 * leave it — an absolute URL, a protocol-relative `//host`, a backslash the
 * browser normalises to a slash, an encoded variant — falls back instead.
 */

/** Paths that must never be returned to, because landing there re-triggers the gate. */
const LOOPING_PATHS = new Set(["/login", "/sign-in", "/sign-up"]);

export function safeReturnTo(target: string | null | undefined, fallback: string): string {
  if (!target) return fallback;

  const trimmed = target.trim();
  // Leading whitespace is stripped by browsers before parsing, so " //evil" is
  // protocol-relative once it reaches navigation. Compare against the trimmed
  // form, and refuse anything that changed under trimming for good measure.
  if (trimmed !== target) return fallback;

  // Must be a root-relative path, and must not be protocol-relative. `\` is
  // normalised to `/` by browsers, so `/\evil.example` is `//evil.example`.
  if (!trimmed.startsWith("/")) return fallback;
  if (trimmed.startsWith("//") || trimmed.startsWith("/\\")) return fallback;
  if (trimmed === "/") return fallback;

  // Percent-encoding can smuggle the same shapes past a raw prefix check.
  let decoded = trimmed;
  try {
    decoded = decodeURIComponent(trimmed);
  } catch {
    return fallback; // malformed escapes: not something to navigate to
  }
  if (decoded.startsWith("//") || decoded.startsWith("/\\")) return fallback;

  // Resolve against a throwaway origin: anything that escapes it is not ours.
  let url: URL;
  try {
    url = new URL(trimmed, "https://internal.invalid");
  } catch {
    return fallback;
  }
  if (url.origin !== "https://internal.invalid") return fallback;

  if (LOOPING_PATHS.has(url.pathname)) return fallback;

  return `${url.pathname}${url.search}${url.hash}`;
}

/** The current location as a return-to value, for capturing before a redirect. */
export function currentReturnTo(): string {
  if (typeof window === "undefined") return "";
  return `${window.location.pathname}${window.location.search}${window.location.hash}`;
}
