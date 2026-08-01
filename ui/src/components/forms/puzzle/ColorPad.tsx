"use client";

import { Check } from "lucide-react";
import { useEffect, useState } from "react";

import { describeArrangement, gradeColor, type ColorSpec } from "@/lib/forms/puzzleSpec";

import { PadAction, PadFooter } from "./PadPrimitives";
import type { PadProps } from "./types";

const UNPAINTED = -1;

/**
 * Pick a colour, tap a panel, paint it.
 *
 * Paint-by-label when a target is set: each region wears the name of the colour
 * it wants, so the activity is match-the-swatch rather than guess-what-I-meant.
 * In `free` mode the labels are gone and any fully-coloured picture counts —
 * that is the version for the youngest crew, where finishing is the win.
 *
 * Tap-only by design: dragging a brush across regions on a touch screen paints
 * whatever the hand brushes past on its way, which is a mess rather than a game.
 */
export function ColorPad({ spec, disabled, submitLabel, submit }: PadProps<ColorSpec>) {
  const [painted, setPainted] = useState<number[]>(() => spec.regions.map(() => UNPAINTED));
  const [brush, setBrush] = useState(0);

  useEffect(() => {
    setPainted(spec.regions.map(() => UNPAINTED));
    setBrush(0);
  }, [spec]);

  const done = painted.every((p) => p !== UNPAINTED);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-center gap-3">
        {spec.palette.map((entry, i) => (
          <button
            key={i}
            type="button"
            disabled={disabled}
            onClick={() => setBrush(i)}
            aria-label={entry.name}
            aria-pressed={brush === i}
            className={`flex min-h-16 items-center gap-3 rounded-2xl border-4 px-4 py-2 text-lg font-semibold transition-all duration-150 active:scale-[0.97] disabled:opacity-50 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-ring ${
              brush === i ? "border-foreground shadow-md" : "border-transparent bg-card"
            }`}
          >
            <span
              className="h-9 w-9 rounded-full border-2 border-background shadow-inner"
              style={{ background: entry.color }}
            />
            <span>{entry.name}</span>
          </button>
        ))}
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {spec.regions.map((region, i) => {
          const fill = painted[i] >= 0 ? spec.palette[painted[i]].color : undefined;
          return (
            <button
              key={i}
              type="button"
              disabled={disabled}
              onClick={() => setPainted((prev) => prev.map((p, j) => (j === i ? brush : p)))}
              className="relative flex min-h-32 flex-col items-center justify-center gap-2 rounded-2xl border-4 border-border p-4 transition-all duration-150 active:scale-[0.98] disabled:opacity-50 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-ring"
              style={fill ? { background: fill } : undefined}
            >
              {/* The label sits on a chip rather than directly on the fill: an
                  author can pick any colour, and dark text on a dark panel would
                  disappear the moment it was painted. */}
              <span className="rounded-full bg-background/85 px-3 py-1 text-lg font-bold text-foreground">
                {region.label}
              </span>
              {!spec.free && region.target >= 0 ? (
                <span className="rounded-full bg-background/85 px-3 py-0.5 text-sm font-medium text-muted-foreground">
                  paint it {spec.palette[region.target].name}
                </span>
              ) : null}
              {painted[i] >= 0 ? (
                <Check className="absolute right-2 top-2 h-5 w-5 rounded-full bg-background/85 p-0.5 text-foreground" />
              ) : null}
            </button>
          );
        })}
      </div>

      <PadFooter
        onReset={() => setPainted(spec.regions.map(() => UNPAINTED))}
        resetDisabled={disabled || painted.every((p) => p === UNPAINTED)}
      >
        <PadAction
          label={submitLabel}
          disabled={disabled || !done}
          onClick={() => submit({ solved: gradeColor(spec, painted), answer: describeArrangement(spec, painted) })}
        />
      </PadFooter>
    </div>
  );
}
