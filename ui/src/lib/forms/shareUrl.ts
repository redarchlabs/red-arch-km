/**
 * Turning a view's link into something ANOTHER device can open.
 *
 * A relative URL is fine for a link the same browser follows, but a QR code is
 * read by a different machine — so `/views/x/kiosk` has to become an absolute
 * address that resolves from across the room. That is the whole job here, plus
 * being honest about the case where it cannot work: a console opened at
 * `localhost` can only produce a QR that says `localhost`, and to the tablet
 * scanning it that means *the tablet*, so it fails with a confusing error.
 * Detecting that up front lets the UI say so instead of the operator finding out
 * by pointing a camera at it.
 */

/** Hosts that only ever mean "the machine asking". */
const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]", "::1", "0.0.0.0"]);

/**
 * Resolve `url` to an absolute address.
 *
 * `host` wins when set (an explicitly configured LAN address survives however
 * the console itself was opened); otherwise the page's own origin is used. An
 * already-absolute `url` is returned untouched. Returns "" when there is nothing
 * resolvable, which callers render as "no link configured" rather than a QR code
 * of the empty string.
 */
export function resolveShareUrl(url: string, origin: string, host?: string | null): string {
  const target = (url ?? "").trim();
  if (!target) return "";
  if (/^https?:\/\//i.test(target)) return target;

  const base = (host ?? "").trim() || (origin ?? "").trim();
  if (!base) return target;
  try {
    // `new URL` handles the join properly — a base with a path, a target with or
    // without a leading slash, query strings, all of it.
    return new URL(target, base.startsWith("http") ? base : `http://${base}`).toString();
  } catch {
    return target;
  }
}

/** True when this address can only be opened on the machine that produced it. */
export function isLoopbackUrl(url: string): boolean {
  try {
    return LOOPBACK_HOSTS.has(new URL(url).hostname.toLowerCase());
  } catch {
    return false;
  }
}
