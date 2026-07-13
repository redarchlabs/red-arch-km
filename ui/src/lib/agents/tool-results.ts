/**
 * Typed, defensive helpers for rendering agent tool results in the console.
 *
 * Tool results arrive as opaque `Record<string, unknown>` over SSE, so every field is
 * narrowed before use. In particular the `web_research` tool returns grounded search
 * results whose `url`s originate from an external provider (Gemini + Google Search
 * grounding) and are therefore untrusted: {@link safeExternalHref} accepts only
 * well-formed absolute `http(s)` URLs, rejecting `javascript:`, `data:`, and other
 * schemes so a hostile citation can never produce a dangerous link.
 */

/** A single grounded citation returned by the `web_research` tool. */
export interface WebResearchSource {
  title?: string;
  url?: string;
  snippet?: string;
}

/** Terminal states of a `batch_generate` / `check_batch` tool result. */
export type BatchStatus =
  | { kind: "done"; text: string }
  | { kind: "processing"; batchId: string | null };

const HTTP_SCHEMES = new Set(["http:", "https:"]);
const BATCH_TOOLS = new Set(["batch_generate", "check_batch"]);

/** Coerce a value to a trimmed, non-empty string, or `undefined`. */
function asText(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() !== "" ? value : undefined;
}

/**
 * Return `url` only if it is a well-formed absolute `http(s)` URL; otherwise `null`.
 * This is the trust boundary for externally-sourced citation links.
 */
export function safeExternalHref(url: unknown): string | null {
  const text = asText(url);
  if (!text) return null;
  try {
    const parsed = new URL(text.trim());
    return HTTP_SCHEMES.has(parsed.protocol) ? parsed.href : null;
  } catch {
    return null;
  }
}

/** Human-friendly host for a URL (drops a leading `www.`); `null` when unparseable. */
export function hostnameOf(url: string): string | null {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return null;
  }
}

/**
 * Normalize a result's `sources` array into typed, renderable citations. URLs are
 * sanitized (unsafe ones are dropped, not rendered as links), and a source is kept
 * only when it carries something worth showing (title, safe URL, or snippet).
 */
export function extractSources(result: Record<string, unknown>): WebResearchSource[] {
  const raw = result.sources;
  if (!Array.isArray(raw)) return [];
  const out: WebResearchSource[] = [];
  for (const item of raw) {
    if (typeof item !== "object" || item === null) continue;
    const rec = item as Record<string, unknown>;
    const title = asText(rec.title);
    const url = safeExternalHref(rec.url);
    const snippet = asText(rec.snippet);
    if (title || url || snippet) {
      out.push({
        ...(title ? { title } : {}),
        ...(url ? { url } : {}),
        ...(snippet ? { snippet } : {}),
      });
    }
  }
  return out;
}

/** The natural-language answer of a result (e.g. `web_research`), or `null`. */
export function resultAnswer(result: Record<string, unknown>): string | null {
  return asText(result.answer) ?? null;
}

/** A tool result's error message, or `null` when the call succeeded. */
export function resultError(result: Record<string, unknown>): string | null {
  return asText(result.error) ?? null;
}

/** Interpret a batch tool's result, or `null` if this is not a recognized batch state. */
export function batchStatus(name: string, result: Record<string, unknown>): BatchStatus | null {
  if (!BATCH_TOOLS.has(name)) return null;
  const status = asText(result.status);
  if (status === "done") return { kind: "done", text: asText(result.text) ?? "" };
  if (status === "processing") return { kind: "processing", batchId: asText(result.batch_id) ?? null };
  return null;
}
