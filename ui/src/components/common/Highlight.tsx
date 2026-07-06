"use client";

import { Fragment } from "react";

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

interface HighlightProps {
  /** Text to render (should already be plain/sanitized). */
  text: string;
  /** Search query — its whitespace-separated terms are highlighted. */
  query: string;
}

/**
 * Render `text` with the query's terms wrapped in <mark>. Terms shorter than
 * two characters are ignored to avoid highlighting noise. Case-insensitive.
 * Renders React nodes (no HTML injection).
 */
export function Highlight({ text, query }: HighlightProps) {
  const terms = query
    .trim()
    .split(/\s+/)
    .filter((t) => t.length >= 2)
    .map(escapeRegExp);

  if (terms.length === 0) return <>{text}</>;

  const re = new RegExp(`(${terms.join("|")})`, "gi");
  const parts = text.split(re);

  return (
    <>
      {parts.map((part, i) =>
        // Odd indices are the captured (matched) groups from split().
        i % 2 === 1 ? (
          <mark key={i} className="rounded bg-primary/20 px-0.5 text-foreground">
            {part}
          </mark>
        ) : (
          <Fragment key={i}>{part}</Fragment>
        ),
      )}
    </>
  );
}
