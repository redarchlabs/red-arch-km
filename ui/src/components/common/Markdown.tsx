"use client";

import DOMPurify from "dompurify";
import { marked } from "marked";

import { cn } from "@/lib/utils";

// GitHub-flavoured markdown; single newlines become <br> so pasted/authored
// text keeps its line breaks.
marked.setOptions({ gfm: true, breaks: true });

interface MarkdownProps {
  content: string;
  className?: string;
  /**
   * Drop images from the output. Set for LLM-authored text: an `![](...)` the
   * model was talked into emitting (via a poisoned document) would otherwise
   * make the reader's browser fetch an attacker URL, leaking whatever the model
   * put in the query string. Document text keeps its images.
   */
  stripImages?: boolean;
  /**
   * Rewrite the parsed HTML before it is sanitized — used to turn chat citation
   * markers into links. Runs on parsed output (not the Markdown source) so it
   * can tell prose from code spans, and its result still goes through DOMPurify.
   */
  transformHtml?: (html: string) => string;
}

/**
 * Render Markdown to sanitized HTML. Used to display a document's ORIGINAL
 * formatted text (headings, lists, code, tables) instead of the whitespace-
 * flattened index chunks. Output is sanitized with DOMPurify before injection.
 */
export function Markdown({
  content,
  className,
  stripImages = false,
  transformHtml,
}: MarkdownProps) {
  const parsed = marked.parse(content, { async: false }) as string;
  const html = DOMPurify.sanitize(
    transformHtml ? transformHtml(parsed) : parsed,
    stripImages ? { FORBID_TAGS: ["img"] } : {},
  );
  return (
    <div
      className={cn("markdown-body", className)}
      // Sanitized above; marked output + DOMPurify is the standard safe pipeline.
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
