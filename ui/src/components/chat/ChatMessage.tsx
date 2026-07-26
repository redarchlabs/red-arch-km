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

const CITATION_CLASS =
  "mx-0.5 rounded bg-primary/10 px-1 text-xs font-medium text-primary no-underline hover:bg-primary/20";

/** Elements whose text is not prose — a `[1]` inside them is not a citation. */
const NON_PROSE = "code, pre, a";

/**
 * Turn `[n]` citation markers into links to the cited passage.
 *
 * Works on the PARSED document rather than the Markdown source, so an array
 * index in `arr[1]` or the label of a real `[1](https://…)` link is left alone —
 * a string-level rewrite can't tell those from a citation. Markers with no
 * matching source stay as plain text. Built with DOM APIs, so href/title values
 * are escaped by the serializer; the result is sanitized by `Markdown`.
 */
function linkifyCitations(html: string, sources: ChatSource[]): string {
  if (typeof DOMParser === "undefined") return html; // no DOM (SSR): leave as-is
  const byNumber = new Map(sources.map((s) => [s.number, s]));
  const doc = new DOMParser().parseFromString(html, "text/html");
  const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT);
  const targets: Text[] = [];
  while (walker.nextNode()) {
    const node = walker.currentNode as Text;
    if (/\[\d+\]/.test(node.data) && !node.parentElement?.closest(NON_PROSE)) targets.push(node);
  }

  for (const node of targets) {
    const fragment = doc.createDocumentFragment();
    let lastIndex = 0;
    for (const match of node.data.matchAll(/\[(\d+)\]/g)) {
      const src = byNumber.get(Number(match[1]));
      if (!src?.document_id || match.index === undefined) continue;
      if (match.index > lastIndex) {
        fragment.append(doc.createTextNode(node.data.slice(lastIndex, match.index)));
      }
      const anchor = doc.createElement("a");
      anchor.setAttribute("href", passageHref(src));
      anchor.setAttribute(
        "title",
        src.snippet ? `${sourceLabel(src)} — "${src.snippet}"` : sourceLabel(src),
      );
      anchor.setAttribute("data-citation", match[1]);
      anchor.setAttribute("class", CITATION_CLASS);
      anchor.textContent = match[0];
      fragment.append(anchor);
      lastIndex = match.index + match[0].length;
    }
    if (lastIndex === 0) continue; // nothing matched a real source
    if (lastIndex < node.data.length) {
      fragment.append(doc.createTextNode(node.data.slice(lastIndex)));
    }
    node.replaceWith(fragment);
  }
  return doc.body.innerHTML;
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
            <Markdown
              content={message.content}
              stripImages
              transformHtml={(html) => linkifyCitations(html, sources)}
            />
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
