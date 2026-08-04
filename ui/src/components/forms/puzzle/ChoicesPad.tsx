"use client";

import { Check, X } from "lucide-react";

import type { ChoicesSpec } from "@/lib/forms/puzzleSpec";

import { PadTile } from "./PadPrimitives";
import type { PadProps } from "./types";

const COLS: Record<number, string> = {
  1: "grid-cols-1",
  2: "grid-cols-1 sm:grid-cols-2",
  3: "grid-cols-1 sm:grid-cols-3",
  4: "grid-cols-2 sm:grid-cols-4",
};

/** The badge down the left of each tile. A single-character value (the A/B/C/D
 * case) is its own marker; anything longer falls back to the position, so an
 * options list of whole words still reads as an ordered set of keys. */
const LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";
function keyFor(value: string, index: number): string {
  const trimmed = value.trim();
  return trimmed.length === 1 ? trimmed.toUpperCase() : (LETTERS[index] ?? String(index + 1));
}

/**
 * Multiple choice as big tap targets.
 *
 * Submits on the tap itself — no confirm step, no scrolling to a separate row of
 * buttons: the option and the way you choose it are the same object.
 *
 * The pad is never told which option is right while it matters (`solved: null`;
 * a workflow compares the reported value against an answer field the view does
 * not name). `correct` arrives only afterwards, when whatever ran the question
 * has published the answer — until then every tile looks the same, and the one
 * you tapped stays marked so you can see what you sent.
 */
export function ChoicesPad({ spec, disabled, submit, picked, correct }: PadProps<ChoicesSpec>) {
  const revealed = correct != null;
  return (
    <div className={`grid gap-3 sm:gap-4 ${COLS[spec.columns] ?? COLS[2]}`}>
      {spec.options.map((opt, i) => {
        const isPicked = picked != null && opt.value === picked;
        const isCorrect = revealed && opt.value === correct;
        const state = isCorrect ? "done" : revealed && isPicked ? "wrong" : isPicked ? "selected" : revealed ? "muted" : "idle";
        const badge = isCorrect
          ? "bg-green-600 text-white"
          : revealed && isPicked
            ? "bg-destructive text-destructive-foreground"
            : isPicked
              ? "bg-primary text-primary-foreground"
              : "bg-muted text-muted-foreground";
        return (
          <PadTile
            key={opt.value}
            disabled={disabled}
            onClick={() => submit({ solved: null, answer: opt.value })}
            state={state}
            // Justified left, not centred: with the key badge pinned at the start,
            // a two-word option and a two-line one begin at the same place.
            className="min-h-20 justify-start px-4 py-4 text-left sm:min-h-24"
          >
            <span
              className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-lg font-black transition-colors ${badge}`}
            >
              {isCorrect ? (
                <Check className="h-6 w-6" strokeWidth={3} />
              ) : revealed && isPicked ? (
                <X className="h-6 w-6" strokeWidth={3} />
              ) : (
                keyFor(opt.value, i)
              )}
            </span>
            <span className="min-w-0 flex-1 text-balance text-lg leading-snug sm:text-xl">{opt.label}</span>
          </PadTile>
        );
      })}
    </div>
  );
}
