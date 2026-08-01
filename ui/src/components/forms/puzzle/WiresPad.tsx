"use client";

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { gradeWires, seedFrom, stableShuffle, describeArrangement, type WiresSpec } from "@/lib/forms/puzzleSpec";

import { PadAction, PadFooter } from "./PadPrimitives";
import { DRAG_SLOP, centerIn, dataIndex, distance, hitTest, pointsDiffer, type Point } from "./dragUtils";
import type { PadProps } from "./types";

const UNCONNECTED = -1;

/**
 * The repair console: drag a lead from a port on the left to its match on the
 * right, and the wire stays drawn between them.
 *
 * Dragging is the point — this is the puzzle that feels like fixing something —
 * but every connection can equally be made by tapping a port and then tapping
 * its partner, which is what actually happens when a small finger doesn't quite
 * land the drop. Tapping a connected port pulls the lead back out.
 *
 * Graded locally (the pairing has to be in the browser to be drawable), so
 * `solved` is a real verdict.
 */
export function WiresPad({ spec, disabled, submitLabel, submit }: PadProps<WiresSpec>) {
  // Right-hand ports are shown in a shuffled order; a port is identified by the
  // PAIR it belongs to, so the shuffle only affects where it is drawn.
  const rightOrder = useMemo(
    () => stableShuffle(spec.pairs.length, seedFrom(spec.pairs.map((p) => p.right).join("|"))),
    [spec],
  );

  const [connections, setConnections] = useState<number[]>(() => spec.pairs.map(() => UNCONNECTED));
  const [armed, setArmed] = useState<number | null>(null);
  const [drag, setDrag] = useState<{ from: number; at: Point } | null>(null);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const leftRefs = useRef<(HTMLElement | null)[]>([]);
  const rightRefs = useRef<(HTMLElement | null)[]>([]);
  const [leftPts, setLeftPts] = useState<Point[]>([]);
  const [rightPts, setRightPts] = useState<Point[]>([]);
  const pressRef = useRef<{ from: number; at: Point; moved: boolean } | null>(null);

  useEffect(() => {
    setConnections(spec.pairs.map(() => UNCONNECTED));
    setArmed(null);
    setDrag(null);
  }, [spec]);

  // Wires are drawn between measured port centres, so the geometry has to be
  // re-read whenever the layout can have changed — mount, resize, font swap.
  const measure = useCallback(() => {
    const container = containerRef.current;
    if (!container) return;
    const read = (els: (HTMLElement | null)[]) =>
      els.map((el) => (el ? centerIn(el, container) : { x: 0, y: 0 }));
    const nextLeft = read(leftRefs.current.slice(0, spec.pairs.length));
    const nextRight = read(rightRefs.current.slice(0, spec.pairs.length));
    setLeftPts((prev) => (pointsDiffer(prev, nextLeft) ? nextLeft : prev));
    setRightPts((prev) => (pointsDiffer(prev, nextRight) ? nextRight : prev));
  }, [spec]);

  useLayoutEffect(() => {
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    if (containerRef.current) ro.observe(containerRef.current);
    return () => ro.disconnect();
  }, [measure]);

  const connect = (from: number, toPair: number) => {
    setConnections((prev) => {
      const next = [...prev];
      // A port takes one lead: giving it to a new left port frees whoever had it.
      const previousOwner = next.indexOf(toPair);
      if (previousOwner >= 0) next[previousOwner] = UNCONNECTED;
      next[from] = toPair;
      return next;
    });
    setArmed(null);
  };

  const onPortDown = (from: number) => (e: React.PointerEvent<HTMLButtonElement>) => {
    if (disabled) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    const at = { x: e.clientX, y: e.clientY };
    pressRef.current = { from, at, moved: false };
    // Pulling on a connected port takes the lead back out, so the drag starts
    // from an empty port and the wire follows the finger.
    setConnections((prev) => {
      if (prev[from] === UNCONNECTED) return prev;
      const next = [...prev];
      next[from] = UNCONNECTED;
      return next;
    });
    const container = containerRef.current;
    setDrag({
      from,
      at: container
        ? { x: e.clientX - container.getBoundingClientRect().left, y: e.clientY - container.getBoundingClientRect().top }
        : { x: 0, y: 0 },
    });
  };

  const onPortMove = (e: React.PointerEvent<HTMLButtonElement>) => {
    const press = pressRef.current;
    if (!press) return;
    if (!press.moved && distance(press.at, { x: e.clientX, y: e.clientY }) > DRAG_SLOP) press.moved = true;
    const container = containerRef.current;
    if (!container) return;
    const base = container.getBoundingClientRect();
    setDrag({ from: press.from, at: { x: e.clientX - base.left, y: e.clientY - base.top } });
  };

  const onPortUp = (e: React.PointerEvent<HTMLButtonElement>) => {
    const press = pressRef.current;
    pressRef.current = null;
    setDrag(null);
    if (!press) return;
    const target = dataIndex(hitTest(e.clientX, e.clientY, "[data-pair]"), "pair");
    if (target >= 0) {
      connect(press.from, target);
      return;
    }
    // Released on nothing. A press that never moved is a tap: arm this port so
    // the next tap on a right-hand port completes the connection.
    setArmed(press.moved ? null : press.from);
  };

  const solvedCount = connections.filter((c) => c !== UNCONNECTED).length;
  const complete = solvedCount === spec.pairs.length;

  const portBase =
    "flex min-h-16 w-full items-center gap-3 rounded-2xl border-2 px-4 py-3 text-left text-lg font-semibold transition-all duration-150 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-ring";

  return (
    <div className="space-y-4">
      <div ref={containerRef} className="relative touch-none select-none" style={{ touchAction: "none" }}>
        {/* Wires sit behind the ports and never take a pointer, so a release
            always hits the port underneath rather than the cable over it. */}
        <svg className="pointer-events-none absolute inset-0 h-full w-full" aria-hidden>
          {connections.map((toPair, from) => {
            if (toPair === UNCONNECTED) return null;
            const a = leftPts[from];
            const rightSlot = rightOrder.indexOf(toPair);
            const b = rightPts[rightSlot];
            if (!a || !b) return null;
            const mid = (a.x + b.x) / 2;
            return (
              <path
                key={from}
                d={`M ${a.x} ${a.y} C ${mid} ${a.y}, ${mid} ${b.y}, ${b.x} ${b.y}`}
                stroke={spec.pairs[from].color}
                strokeWidth={7}
                strokeLinecap="round"
                fill="none"
              />
            );
          })}
          {drag && leftPts[drag.from] ? (
            <path
              d={`M ${leftPts[drag.from].x} ${leftPts[drag.from].y} C ${
                (leftPts[drag.from].x + drag.at.x) / 2
              } ${leftPts[drag.from].y}, ${(leftPts[drag.from].x + drag.at.x) / 2} ${drag.at.y}, ${
                drag.at.x
              } ${drag.at.y}`}
              stroke={spec.pairs[drag.from].color}
              strokeWidth={7}
              strokeLinecap="round"
              strokeDasharray="2 12"
              fill="none"
            />
          ) : null}
        </svg>

        <div className="relative grid grid-cols-2 gap-x-10 gap-y-3 sm:gap-x-24">
          <div className="space-y-3">
            {spec.pairs.map((pair, i) => (
              <button
                key={i}
                type="button"
                disabled={disabled}
                ref={(el) => {
                  leftRefs.current[i] = el;
                }}
                onPointerDown={onPortDown(i)}
                onPointerMove={onPortMove}
                onPointerUp={onPortUp}
                onPointerCancel={() => {
                  pressRef.current = null;
                  setDrag(null);
                }}
                className={`${portBase} ${
                  armed === i
                    ? "border-primary bg-primary/10 ring-4 ring-primary/20"
                    : connections[i] !== UNCONNECTED
                      ? "border-green-500 bg-green-500/10"
                      : "border-border bg-card"
                }`}
              >
                <span
                  className="h-6 w-6 shrink-0 rounded-full border-2 border-background shadow"
                  style={{ background: pair.color }}
                />
                <span className="min-w-0 flex-1">{pair.left}</span>
              </button>
            ))}
          </div>

          <div className="space-y-3">
            {rightOrder.map((pairIndex) => {
              const takenBy = connections.indexOf(pairIndex);
              const slot = rightOrder.indexOf(pairIndex);
              return (
                <button
                  key={pairIndex}
                  type="button"
                  data-pair={pairIndex}
                  disabled={disabled}
                  ref={(el) => {
                    rightRefs.current[slot] = el;
                  }}
                  onClick={() => {
                    if (armed != null) connect(armed, pairIndex);
                  }}
                  className={`${portBase} justify-end ${
                    takenBy >= 0 ? "border-green-500 bg-green-500/10" : "border-border bg-card"
                  }`}
                >
                  <span className="min-w-0 flex-1 text-right">{spec.pairs[pairIndex].right}</span>
                  <span
                    className="h-6 w-6 shrink-0 rounded-full border-2 border-background shadow"
                    style={{ background: takenBy >= 0 ? spec.pairs[takenBy].color : "var(--muted-foreground, #94a3b8)" }}
                  />
                </button>
              );
            })}
          </div>
        </div>
      </div>

      <p className="text-center text-base text-muted-foreground">
        {armed != null
          ? `Now tap where “${spec.pairs[armed].left}” connects.`
          : `${solvedCount} of ${spec.pairs.length} connected — drag a wire, or tap both ends.`}
      </p>

      <PadFooter
        onReset={() => {
          setConnections(spec.pairs.map(() => UNCONNECTED));
          setArmed(null);
        }}
        resetDisabled={disabled || !solvedCount}
      >
        <PadAction
          label={submitLabel}
          disabled={disabled || !complete}
          onClick={() =>
            submit({ solved: gradeWires(spec, connections), answer: describeArrangement(spec, connections) })
          }
        />
      </PadFooter>
    </div>
  );
}
