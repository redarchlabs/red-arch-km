"use client";

/**
 * A tool's arguments and results, as something a person reads rather than parses.
 *
 * These payloads are genuinely dynamic — every tool has its own shape and new tools
 * arrive without this file knowing — so the previous version dumped pretty-printed
 * JSON, which is honest and unreadable: braces, quotes and commas outnumbering the
 * facts, and the one line that matters ("status": 404) sitting mid-blob.
 *
 * Nothing here knows any tool's schema. It reads the *shape* of a value and picks a
 * presentation for it: an object becomes labelled rows, a list of plain values
 * becomes a comma-separated line, a long string gets the full width, and anything
 * genuinely nested falls back to JSON at the point where nesting starts rather than
 * for the whole payload. That is enough to make a fetch result read like a page's
 * facts and a delegation read like a brief, without either being anticipated.
 *
 * The raw text stays one click away. This view drops nothing, but a reader who wants
 * the exact bytes should not have to take the renderer's word for it.
 */

import { useState } from "react";

/** Longest single value shown before it is cut. Enough for a page's text excerpt or
 *  a long delegation brief; short of pasting a whole document into the panel. */
const MAX_CHARS = 4_000;

/** Past this, a value stops being a phrase on a row and becomes a paragraph. */
const INLINE_CHARS = 80;

/** How deep to keep laying values out before handing over to JSON. Two levels covers
 *  every payload these tools actually produce; deeper is a data structure, not prose. */
const MAX_DEPTH = 2;

export function formatPayload(value: unknown): string {
  if (value === undefined || value === null) return "";
  if (typeof value === "string") return value;
  // A tool called with no arguments prints "{}", which is a heading and a pair of
  // braces telling the reader nothing. Empty is empty.
  if (typeof value === "object" && Object.keys(value as object).length === 0)
    return "";
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    // Circular or otherwise unserialisable — better to show its shape than to
    // drop the step's only evidence.
    return String(value);
  }
}

/** `meta_description` reads as "meta description". The key still matches the API,
 *  which matters when someone is comparing this against a tool's docs. */
export function humanise(key: string): string {
  return key.replace(/[_-]+/g, " ").trim();
}

function clamp(text: string): { body: string; cut: number } {
  if (text.length <= MAX_CHARS) return { body: text, cut: 0 };
  return { body: text.slice(0, MAX_CHARS), cut: text.length - MAX_CHARS };
}

function isPlain(value: unknown): boolean {
  return (
    value === null || ["string", "number", "boolean"].includes(typeof value)
  );
}

/** One primitive, as a person would say it aloud. */
function Scalar({ value }: { value: unknown }) {
  if (value === null || value === undefined)
    return <span className="italic text-muted-foreground">none</span>;
  if (typeof value === "boolean")
    return <span className="font-medium">{value ? "yes" : "no"}</span>;
  if (typeof value === "number")
    return <span className="font-medium tabular-nums">{value}</span>;
  const text = String(value);
  if (!text) return <span className="italic text-muted-foreground">empty</span>;
  return <span className="break-words">{text}</span>;
}

function Json({ value }: { value: unknown }) {
  const { body, cut } = clamp(formatPayload(value));
  return (
    <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all rounded bg-muted/60 p-1.5 font-mono text-[11px] leading-snug">
      {body}
      {cut > 0 ? `\n… ${cut.toLocaleString()} more characters` : ""}
    </pre>
  );
}

function Value({ value, depth }: { value: unknown; depth: number }) {
  if (isPlain(value)) return <Scalar value={value} />;

  if (Array.isArray(value)) {
    if (value.length === 0)
      return <span className="italic text-muted-foreground">none</span>;
    // A list of plain values is a sentence, not a structure.
    if (value.every(isPlain))
      return (
        <span className="break-words">{value.map(String).join(", ")}</span>
      );
    if (depth >= MAX_DEPTH) return <Json value={value} />;
    return (
      <div className="space-y-1">
        {value.map((item, i) => (
          <div key={i} className="border-l pl-2">
            <Value value={item} depth={depth + 1} />
          </div>
        ))}
      </div>
    );
  }

  if (depth >= MAX_DEPTH) return <Json value={value} />;
  return <Rows value={value as Record<string, unknown>} depth={depth + 1} />;
}

function Rows({
  value,
  depth,
}: {
  value: Record<string, unknown>;
  depth: number;
}) {
  const entries = Object.entries(value);
  if (!entries.length)
    return <span className="italic text-muted-foreground">none</span>;
  return (
    <div className="space-y-0.5">
      {entries.map(([key, item]) => {
        // A long string is a paragraph with a heading, not a cell in a table: the
        // two-column grid squeezes a delegation brief into a ribbon down the page.
        const block =
          (typeof item === "string" && item.length > INLINE_CHARS) ||
          (!isPlain(item) && !(Array.isArray(item) && item.every(isPlain)));
        return (
          <div
            key={key}
            className={
              block ? "" : "grid grid-cols-[minmax(0,9rem)_1fr] gap-x-3"
            }
          >
            <div className="truncate text-muted-foreground" title={key}>
              {humanise(key)}
            </div>
            <div className={block ? "mt-0.5 whitespace-pre-wrap" : "min-w-0"}>
              <Value value={item} depth={depth} />
            </div>
          </div>
        );
      })}
    </div>
  );
}

/** A tool payload under its heading, with the raw text one click away. */
export function PayloadView({
  label,
  value,
}: {
  label: string;
  value: unknown;
}) {
  const [raw, setRaw] = useState(false);
  const text = formatPayload(value);
  if (!text.trim()) return null;
  const structured = !isPlain(value);
  return (
    <div className="mt-1.5">
      <div className="flex items-baseline gap-2">
        <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
          {label}
        </span>
        {structured ? (
          <button
            type="button"
            onClick={() => setRaw((r) => !r)}
            className="text-[10px] text-muted-foreground underline underline-offset-2 hover:text-foreground"
          >
            {raw ? "formatted" : "raw"}
          </button>
        ) : null}
      </div>
      <div className="mt-0.5 max-h-56 overflow-auto rounded bg-muted/40 px-2 py-1.5 leading-relaxed">
        {raw || !structured ? (
          raw ? (
            <Json value={value} />
          ) : (
            <PlainText text={text} />
          )
        ) : (
          <Value value={value} depth={0} />
        )}
      </div>
    </div>
  );
}

function PlainText({ text }: { text: string }) {
  const { body, cut } = clamp(text);
  return (
    <p className="whitespace-pre-wrap break-words">
      {body}
      {cut > 0 ? (
        <span className="text-muted-foreground">
          {`\n… ${cut.toLocaleString()} more characters`}
        </span>
      ) : null}
    </p>
  );
}
