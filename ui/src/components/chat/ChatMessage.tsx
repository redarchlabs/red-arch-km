"use client";

import DOMPurify from "dompurify";
import Link from "next/link";

import { Markdown } from "@/components/common/Markdown";
import type { AgentTraceStep, ChatSource } from "@/lib/api/search";
import { cn } from "@/lib/utils";

import { AgentTrace } from "./AgentTrace";
import { ThinkingIndicator } from "./ThinkingIndicator";

export interface Message {
  /** Stable ID assigned when the message is appended; used as React key. */
  id: string;
  role: "user" | "assistant";
  content: string;
  sources?: ChatSource[];
  streaming?: boolean;
  /** Agentic-mode reasoning trace (present only for fact-engine answers). */
  agentTrace?: AgentTraceStep[];
  /** Citations the answer made that no gathered evidence supported. */
  unsupportedCitations?: string[];
}

interface ChatMessageProps {
  message: Message;
}

/**
 * React escapes text by default, but we additionally strip HTML via DOMPurify
 * so accidental markup in LLM output or pasted user prompts never escapes
 * the plain-text rendering — belt-and-suspenders.
 */
function sanitize(text: string): string {
  return DOMPurify.sanitize(text, { ALLOWED_TAGS: [], ALLOWED_ATTR: [] });
}

/**
 * Deep-link to the cited passage: the document reader anchors each indexed
 * chunk as `#chunk-<order>`. Falls back to the document top when the source
 * carries no chunk_order (older persisted messages predate passage-level
 * citations).
 */
function passageHref(src: ChatSource): string {
  const base = `/documents/${src.document_key}`;
  return src.chunk_order != null ? `${base}#chunk-${src.chunk_order}` : base;
}

/** Human label for a source: document title, plus the section when known. */
function sourceLabel(src: ChatSource): string {
  const title = src.document_title || src.document_key;
  return src.section ? `${title} — ${src.section}` : title;
}

/**
 * De-duplicate sources to one entry per *passage* (document + chunk) and assign
 * a stable 1-based number. The backend already numbers per passage, but we
 * dedupe defensively. Sources without a chunk_order (older persisted messages)
 * key on the document alone, preserving the old one-per-document behaviour.
 */
function dedupeSources(sources: ChatSource[]): ChatSource[] {
  const byKey = new Map<string, ChatSource>();
  for (const s of sources) {
    const docKey = s.document_id || s.document_key;
    const key = s.chunk_order != null ? `${docKey}#${s.chunk_order}` : docKey;
    if (!byKey.has(key)) byKey.set(key, s);
  }
  return [...byKey.values()].map((s, i) => ({ ...s, number: s.number ?? i + 1 }));
}

/** Citation numbers (`[n]`) that appear in the answer text. */
function citedNumbers(text: string): Set<number> {
  const cited = new Set<number>();
  for (const match of text.matchAll(/\[(\d+)\]/g)) {
    cited.add(Number(match[1]));
  }
  return cited;
}

/** Escape a value destined for an HTML attribute in the generated citation anchor. */
function escapeAttribute(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

const CITATION_CLASS =
  "mx-0.5 rounded bg-primary/10 px-1 text-xs font-medium text-primary no-underline hover:bg-primary/20";

/**
 * Rewrite inline `[n]` citation markers into anchors linking to the cited
 * passage, leaving the rest of the answer untouched so Markdown still parses.
 * Markers with no matching source stay literal — escaped so Markdown reads them
 * as text rather than as link syntax.
 *
 * The result is Markdown-with-inline-HTML; `Markdown` sanitizes the rendered
 * output, so these anchors (and anything the model itself emitted) go through
 * DOMPurify before reaching the DOM.
 */
function withCitationLinks(text: string, sources: ChatSource[]): string {
  const byNumber = new Map(sources.map((s) => [s.number, s]));
  return text.replace(/\[(\d+)\]/g, (marker, digits: string) => {
    const src = byNumber.get(Number(digits));
    if (!src?.document_id) return `\\[${digits}\\]`;
    const title = src.snippet ? `${sourceLabel(src)} — "${src.snippet}"` : sourceLabel(src);
    return (
      `<a href="${escapeAttribute(passageHref(src))}"` +
      ` title="${escapeAttribute(title)}"` +
      ` data-citation="${digits}"` +
      ` class="${CITATION_CLASS}">${marker}</a>`
    );
  });
}

export function ChatMessage({ message }: ChatMessageProps) {
  const isUser = message.role === "user";
  const sources = !isUser && message.sources ? dedupeSources(message.sources) : [];
  // The Sources footer lists only passages the answer actually cites. While the
  // answer is still streaming the markers have not all arrived, so the full list
  // is shown until it settles — otherwise the footer would visibly churn. When a
  // finished answer carries no usable [n] markers (older persisted messages, or
  // a model that answered without citing), fall back to listing everything
  // retrieved rather than hiding provenance entirely.
  const cited = citedNumbers(message.content);
  const citedSources = sources.filter((s) => cited.has(s.number as number));
  const listedSources = message.streaming || citedSources.length === 0 ? sources : citedSources;
  const awaitingFirstToken = !isUser && !!message.streaming && message.content.trim() === "";

  return (
    <div className={cn("flex", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[75%] rounded-lg px-4 py-3 text-sm",
          isUser ? "bg-primary text-primary-foreground" : "bg-muted",
        )}
      >
        {isUser ? (
          // User text is shown verbatim — a typed "#" or "*" is punctuation,
          // not formatting.
          <div className="whitespace-pre-wrap">{sanitize(message.content)}</div>
        ) : awaitingFirstToken ? (
          // Nothing to render yet: say what the answer is waiting on rather
          // than leaving a bare blinking dot.
          <ThinkingIndicator
            label={sources.length > 0 ? "Writing the answer…" : "Searching your documents…"}
          />
        ) : (
          <>
            <Markdown content={withCitationLinks(message.content, sources)} stripImages />
            {message.streaming ? (
              <span
                aria-label="streaming"
                className="ml-1 inline-block h-3.5 w-1.5 animate-pulse rounded-sm bg-current align-text-bottom"
              />
            ) : null}
          </>
        )}

        {message.unsupportedCitations && message.unsupportedCitations.length > 0 ? (
          <p className="mt-2 text-xs text-amber-600 dark:text-amber-500">
            ⚠ Some citations ({message.unsupportedCitations.join(", ")}) were not grounded in
            retrieved evidence.
          </p>
        ) : null}

        {!isUser && message.agentTrace ? (
          <AgentTrace steps={message.agentTrace} live={message.streaming} />
        ) : null}

        {listedSources.length > 0 ? (
          <div className="mt-3 border-t pt-2">
            <p className="mb-1 text-xs font-medium text-muted-foreground">Sources</p>
            <ol className="space-y-1.5">
              {listedSources.map((src) => (
                <li
                  key={`${src.document_id || src.document_key}-${src.chunk_order ?? src.number}`}
                  className="text-xs"
                >
                  <Link
                    href={passageHref(src)}
                    className="inline-flex items-baseline gap-1.5 text-muted-foreground hover:text-foreground hover:underline"
                  >
                    <span className="font-medium text-primary">[{src.number}]</span>
                    <span>{sourceLabel(src)}</span>
                  </Link>
                  {src.snippet ? (
                    <p className="mt-0.5 pl-6 italic text-muted-foreground/80">“{src.snippet}”</p>
                  ) : null}
                </li>
              ))}
            </ol>
          </div>
        ) : null}
      </div>
    </div>
  );
}
