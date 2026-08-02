"use client";

import type { ChoicesSpec } from "@/lib/forms/puzzleSpec";

import { PadTile } from "./PadPrimitives";
import type { PadProps } from "./types";

const COLS: Record<number, string> = {
  1: "grid-cols-1",
  2: "grid-cols-1 sm:grid-cols-2",
  3: "grid-cols-1 sm:grid-cols-3",
  4: "grid-cols-2 sm:grid-cols-4",
};

/**
 * Multiple choice as big tap targets.
 *
 * Submits on the tap itself — no confirm step. The pad is never told which
 * option is right (`solved: null`); a workflow compares the reported value
 * against an answer field the view does not name, so the answer stays on the
 * server.
 */
export function ChoicesPad({ spec, disabled, submit }: PadProps<ChoicesSpec>) {
  return (
    <div className={`grid gap-4 ${COLS[spec.columns] ?? COLS[2]}`}>
      {spec.options.map((opt) => (
        <PadTile
          key={opt.value}
          disabled={disabled}
          onClick={() => submit({ solved: null, answer: opt.value })}
          className="min-h-28"
        >
          {opt.label}
        </PadTile>
      ))}
    </div>
  );
}
