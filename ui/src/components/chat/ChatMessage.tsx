"use client";

import DOMPurify from "dompurify";
import Link from "next/link";
import { Fragment } from "react";

import type { ChatSource } from "@/lib/api/search";
import { cn } from "@/lib/utils";

export interface Message {
  /** Stable ID assigned when the message is appended; used as React key. */
  id: string;
  role: "user" | "assistant";
  content: string;
  sources?: ChatSource[];
  streaming?: boolean;
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
 * Collapse sources to one entry per document and assign a stable 1-based
 * number. The backend already dedupes and numbers, but older persisted
 * messages may still carry per-chunk duplicates — so we dedupe defensively.
 */
function dedupeSources(sources: ChatSource[]): ChatSource[] {
  const byId = new Map<string, ChatSource>();
  for (const s of sources) {
    const key = s.document_id || s.document_key;
    if (!byId.has(key)) byId.set(key, s);
  }
  return [...byId.values()].map((s, i) => ({ ...s, number: s.number ?? i + 1 }));
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
          href={`/documents/${src.document_key}`}
          title={src.document_title || src.document_key}
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

        {sources.length > 0 ? (
          <div className="mt-3 border-t pt-2">
            <p className="mb-1 text-xs font-medium text-muted-foreground">Sources</p>
            <ol className="space-y-1">
              {sources.map((src) => (
                <li key={src.document_id || src.document_key} className="text-xs">
                  <Link
                    href={`/documents/${src.document_key}`}
                    className="inline-flex items-center gap-1.5 text-muted-foreground hover:text-foreground hover:underline"
                  >
                    <span className="font-medium text-primary">[{src.number}]</span>
                    <span>{src.document_title || src.document_key}</span>
                  </Link>
                </li>
              ))}
            </ol>
          </div>
        ) : null}
      </div>
    </div>
  );
}
