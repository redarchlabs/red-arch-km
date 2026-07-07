"use client";

import DOMPurify from "dompurify";
import Link from "next/link";
import { Fragment } from "react";

import type { AgentTraceStep, ChatSource } from "@/lib/api/search";
import { cn } from "@/lib/utils";

import { AgentTrace } from "./AgentTrace";

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

/**
 * Render answer text, turning inline `[n]` citation markers into links to the
 * matching source document. Segments between markers are plain (React-escaped)
 * text; an `[n]` with no matching source is left as literal text.
 */
function renderWithCitations(text: string, sources: ChatSource[]): React.ReactNode[] {
  const byNumber = new Map(sources.map((s) => [s.number, s]));
  const nodes: React.ReactNode[] = [];
  const regex = /\[(\d+)\]/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let k = 0;

  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(
        <Fragment key={`t${k}`}>{sanitize(text.slice(lastIndex, match.index))}</Fragment>,
      );
    }
    const n = Number(match[1]);
    const src = byNumber.get(n);
    if (src?.document_id) {
      nodes.push(
        <Link
          key={`c${k}`}
          href={passageHref(src)}
          title={src.snippet ? `${sourceLabel(src)} — "${src.snippet}"` : sourceLabel(src)}
          className="mx-0.5 rounded bg-primary/10 px-1 text-xs font-medium text-primary no-underline hover:bg-primary/20"
        >
          [{n}]
        </Link>,
      );
    } else {
      nodes.push(<Fragment key={`c${k}`}>{match[0]}</Fragment>);
    }
    lastIndex = regex.lastIndex;
    k += 1;
  }
  if (lastIndex < text.length) {
    nodes.push(<Fragment key={`t${k}`}>{sanitize(text.slice(lastIndex))}</Fragment>);
  }
  return nodes;
}

export function ChatMessage({ message }: ChatMessageProps) {
  const isUser = message.role === "user";
  const sources = !isUser && message.sources ? dedupeSources(message.sources) : [];

  return (
    <div className={cn("flex", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[75%] rounded-lg px-4 py-3 text-sm",
          isUser ? "bg-primary text-primary-foreground" : "bg-muted",
        )}
      >
        <div className="whitespace-pre-wrap">
          {isUser ? sanitize(message.content) : renderWithCitations(message.content, sources)}
        </div>
        {message.streaming ? (
          <span
            aria-label="streaming"
            className="ml-1 inline-block h-2 w-2 animate-pulse rounded-full bg-current align-middle"
          />
        ) : null}

        {message.unsupportedCitations && message.unsupportedCitations.length > 0 ? (
          <p className="mt-2 text-xs text-amber-600 dark:text-amber-500">
            ⚠ Some citations ({message.unsupportedCitations.join(", ")}) were not grounded in
            retrieved evidence.
          </p>
        ) : null}

        {!isUser && message.agentTrace ? (
          <AgentTrace steps={message.agentTrace} live={message.streaming} />
        ) : null}

        {sources.length > 0 ? (
          <div className="mt-3 border-t pt-2">
            <p className="mb-1 text-xs font-medium text-muted-foreground">Sources</p>
            <ol className="space-y-1.5">
              {sources.map((src) => (
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
