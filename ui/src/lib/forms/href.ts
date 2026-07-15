/**
 * Safe link-href helpers shared by the FormRenderer (link columns, per-row record_list
 * links, and templated button hrefs). Kept pure + dependency-free so they're unit-testable
 * and can't drift between the three call sites.
 */

/** Reject a non-http(s) scheme (`javascript:`, `data:`, `vbscript:`, …) so a stored,
 * author-supplied link can never become an XSS vector when it's rendered/navigated.
 * Relative URLs (no scheme) and http(s) URLs pass through unchanged. */
export function safeHref(url: string): string {
  const scheme = /^\s*([a-zA-Z][a-zA-Z0-9+.-]*):/.exec(url);
  if (scheme && !/^https?$/i.test(scheme[1])) return "#";
  return url;
}

/** Fill `{token}` placeholders from a flat record dict — `{id}` and each `{<field>}`
 * resolve to that key on the record, URL-encoded; an absent token becomes an empty
 * string. The result is scheme-checked. Used for a record_list's per-row link and a
 * button's templated href, where values are a flat `{slug: value}` map. */
export function fillTokens(template: string, record: Record<string, unknown>): string {
  const url = template.replace(/\{(\w+)\}/g, (_, key: string) => {
    const v = record[key];
    return v == null ? "" : encodeURIComponent(String(v));
  });
  return safeHref(url);
}
