"use client";

import { useEffect, useRef, useState } from "react";

import { describeArrangement, gradeSort, type SortSpec } from "@/lib/forms/puzzleSpec";

import { PadAction, PadFooter } from "./PadPrimitives";
import { DRAG_SLOP, dataIndex, distance, hitTest, type Point } from "./dragUtils";
import type { PadProps } from "./types";

const TRAY = -1;

/**
 * Drag each item into the bin it belongs in — classify, triage, stow the cargo.
 *
 * The dragged chip is followed by a ghost that ignores pointer events, so the
 * hit-test on release finds the bin underneath rather than the chip itself. As
 * with the wires pad, tapping a chip and then tapping a bin does the same job
 * for anyone who can't hold a drag; tapping a placed chip returns it to the tray.
 *
 * Graded locally — the bin each item belongs to has to be in the browser.
 */
export function SortPad({ spec, disabled, submitLabel, submit }: PadProps<SortSpec>) {
  const [placed, setPlaced] = useState<number[]>(() => spec.items.map(() => TRAY));
  const [armed, setArmed] = useState<number | null>(null);
  const [drag, setDrag] = useState<{ item: number; at: Point } | null>(null);
  const pressRef = useRef<{ item: number; at: Point; moved: boolean } | null>(null);

  useEffect(() => {
    setPlaced(spec.items.map(() => TRAY));
    setArmed(null);
    setDrag(null);
  }, [spec]);

  const place = (item: number, bin: number) => {
    setPlaced((prev) => prev.map((b, i) => (i === item ? bin : b)));
    setArmed(null);
  };

  const onChipDown = (item: number) => (e: React.PointerEvent<HTMLButtonElement>) => {
    if (disabled) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    pressRef.current = { item, at: { x: e.clientX, y: e.clientY }, moved: false };
  };

  const onChipMove = (e: React.PointerEvent<HTMLButtonElement>) => {
    const press = pressRef.current;
    if (!press) return;
    if (!press.moved && distance(press.at, { x: e.clientX, y: e.clientY }) > DRAG_SLOP) press.moved = true;
    if (press.moved) setDrag({ item: press.item, at: { x: e.clientX, y: e.clientY } });
  };

  const onChipUp = (e: React.PointerEvent<HTMLButtonElement>) => {
    const press = pressRef.current;
    pressRef.current = null;
    setDrag(null);
    if (!press) return;
    if (!press.moved) {
      // A tap: toggle this chip as the armed one, or send a placed chip home.
      if (placed[press.item] !== TRAY) place(press.item, TRAY);
      else setArmed((prev) => (prev === press.item ? null : press.item));
      return;
    }
    const bin = dataIndex(hitTest(e.clientX, e.clientY, "[data-bin]"), "bin");
    if (bin >= 0) place(press.item, bin);
    else if (hitTest(e.clientX, e.clientY, "[data-tray]")) place(press.item, TRAY);
  };

  const chipClass =
    "min-h-16 cursor-grab touch-none select-none rounded-2xl border-2 px-5 py-3 text-lg font-semibold transition-all duration-150 active:cursor-grabbing active:scale-[0.98] disabled:opacity-50 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-ring";

  const chip = (item: number) => (
    <button
      key={item}
      type="button"
      disabled={disabled}
      onPointerDown={onChipDown(item)}
      onPointerMove={onChipMove}
      onPointerUp={onChipUp}
      onPointerCancel={() => {
        pressRef.current = null;
        setDrag(null);
      }}
      className={`${chipClass} ${
        armed === item
          ? "border-primary bg-primary/10 ring-4 ring-primary/20"
          : "border-border bg-card hover:border-primary/60"
      } ${drag?.item === item ? "opacity-30" : ""}`}
      style={{ touchAction: "none" }}
    >
      {spec.items[item].label}
    </button>
  );

  const inTray = spec.items.map((_, i) => i).filter((i) => placed[i] === TRAY);
  const complete = inTray.length === 0;
  const anyPlaced = inTray.length < spec.items.length;

  return (
    <div className="space-y-4">
      <div
        data-tray
        className="flex min-h-24 flex-wrap items-center gap-3 rounded-2xl border-2 border-dashed border-border bg-muted/30 p-4"
      >
        {inTray.length ? (
          inTray.map(chip)
        ) : (
          <p className="w-full text-center text-base text-muted-foreground">Everything is stowed.</p>
        )}
      </div>

      <div className={`grid gap-4 ${spec.bins.length > 2 ? "sm:grid-cols-3" : "sm:grid-cols-2"}`}>
        {spec.bins.map((bin, b) => {
          const contents = spec.items.map((_, i) => i).filter((i) => placed[i] === b);
          return (
            <div
              key={b}
              data-bin={b}
              onClick={() => {
                if (armed != null) place(armed, b);
              }}
              className={`min-h-40 space-y-3 rounded-2xl border-2 p-4 transition-colors ${
                armed != null ? "border-primary/60 bg-primary/5" : "border-border bg-card"
              }`}
            >
              <p className="text-center text-lg font-bold">{bin}</p>
              <div className="flex flex-wrap justify-center gap-3">{contents.map(chip)}</div>
            </div>
          );
        })}
      </div>

      {drag ? (
        // The ghost must not take pointer events, or every drop lands on it.
        <div
          className="pointer-events-none fixed z-50 -translate-x-1/2 -translate-y-1/2 rounded-2xl border-2 border-primary bg-card px-5 py-3 text-lg font-semibold shadow-xl"
          style={{ left: drag.at.x, top: drag.at.y }}
        >
          {spec.items[drag.item].label}
        </div>
      ) : null}

      <p className="text-center text-base text-muted-foreground">
        {armed != null
          ? `Now tap the bin for “${spec.items[armed].label}”.`
          : "Drag each item into a bin — or tap the item, then the bin."}
      </p>

      <PadFooter
        onReset={() => {
          setPlaced(spec.items.map(() => TRAY));
          setArmed(null);
        }}
        resetDisabled={disabled || !anyPlaced}
      >
        <PadAction
          label={submitLabel}
          disabled={disabled || !complete}
          onClick={() => submit({ solved: gradeSort(spec, placed), answer: describeArrangement(spec, placed) })}
        />
      </PadFooter>
    </div>
  );
}
