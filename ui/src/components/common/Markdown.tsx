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
}

/**
 * Render Markdown to sanitized HTML. Used to display a document's ORIGINAL
 * formatted text (headings, lists, code, tables) instead of the whitespace-
 * flattened index chunks. Output is sanitized with DOMPurify before injection.
 */
export function Markdown({ content, className, stripImages = false }: MarkdownProps) {
  const html = DOMPurify.sanitize(
    marked.parse(content, { async: false }) as string,
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
