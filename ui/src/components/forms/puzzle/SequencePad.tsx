"use client";

import { useEffect, useMemo, useState } from "react";

import { gradeSequence, seedFrom, stableShuffle, type SequenceSpec } from "@/lib/forms/puzzleSpec";

import { OrderBadge, PadAction, PadFooter, PadTile } from "./PadPrimitives";
import type { PadProps } from "./types";

/**
 * Tap the steps into the right order — a launch checklist, a repair procedure.
 *
 * Tapping (not dragging) to order is the accessible choice and the reliable one
 * on a tablet: a tap can't be dropped in the gap between two targets. Tapping a
 * numbered step again takes it back out, so a mistake costs one tap.
 *
 * Graded locally: the correct order has to be in the browser for the pad to know
 * when the person is done, so `solved` is a real verdict here.
 */
export function SequencePad({ spec, disabled, submitLabel, submit }: PadProps<SequenceSpec>) {
  // Shuffle once per puzzle: a re-shuffle mid-solve would move a step out from
  // under a finger. Seeded off the content so the same puzzle looks the same
  // each time it is drawn, and two puzzles don't share a layout.
  const display = useMemo(
    () => stableShuffle(spec.items.length, seedFrom(spec.items.join("|"))),
    [spec],
  );
  const [tapped, setTapped] = useState<number[]>([]);

  useEffect(() => {
    setTapped([]);
  }, [spec]);

  const positionOf = (item: number) => tapped.indexOf(item);

  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2">
        {display.map((item) => {
          const pos = positionOf(item);
          const chosen = pos >= 0;
          return (
            <PadTile
              key={item}
              disabled={disabled}
              state={chosen ? "selected" : "idle"}
              onClick={() =>
                setTapped((prev) => (chosen ? prev.filter((i) => i !== item) : [...prev, item]))
              }
            >
              {chosen ? <OrderBadge n={pos + 1} /> : null}
              <span>{spec.items[item]}</span>
            </PadTile>
          );
        })}
      </div>

      <PadFooter onReset={() => setTapped([])} resetDisabled={disabled || !tapped.length}>
        <PadAction
          label={submitLabel}
          disabled={disabled || tapped.length !== spec.items.length}
          onClick={() =>
            submit({
              solved: gradeSequence(spec, tapped),
              answer: tapped.map((i) => spec.items[i]).join(" → "),
            })
          }
        />
      </PadFooter>
    </div>
  );
}
