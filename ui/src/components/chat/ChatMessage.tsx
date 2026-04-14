"use client";

import DOMPurify from "dompurify";
import { FileText } from "lucide-react";

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

export function ChatMessage({ message }: ChatMessageProps) {
  const isUser = message.role === "user";

  return (
    <div className={cn("flex", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[75%] rounded-lg px-4 py-3 text-sm",
          isUser ? "bg-primary text-primary-foreground" : "bg-muted",
        )}
      >
        <div className="whitespace-pre-wrap">{sanitize(message.content)}</div>
        {message.streaming ? (
          <span
            aria-label="streaming"
            className="ml-1 inline-block h-2 w-2 animate-pulse rounded-full bg-current align-middle"
          />
        ) : null}

        {!isUser && message.sources && message.sources.length > 0 ? (
          <div className="mt-3 border-t pt-2">
            <p className="mb-1 text-xs font-medium text-muted-foreground">Sources</p>
            <ul className="space-y-1">
              {message.sources.map((src, idx) => (
                <li
                  key={`${src.document_id}-${idx}`}
                  className="flex items-center gap-1.5 text-xs text-muted-foreground"
                >
                  <FileText className="h-3 w-3" />
                  <span>{src.document_title || src.document_key}</span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </div>
  );
}
