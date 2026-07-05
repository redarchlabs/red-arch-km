import TurndownService from "turndown";

// Instantiated once; Turndown is pure JS and safe to construct at module load.
const turndown = new TurndownService({
  headingStyle: "atx",
  bulletListMarker: "-",
  codeBlockStyle: "fenced",
  emDelimiter: "*",
});

/**
 * Convert rich-text editor HTML into clean Markdown before it enters the
 * ingest pipeline. Embeddings/retrieval work far better on Markdown than on
 * raw HTML (tags/attributes are noise), so the editor authors in HTML but the
 * document is stored and indexed as Markdown.
 */
export function htmlToMarkdown(html: string): string {
  return turndown.turndown(html).trim();
}
