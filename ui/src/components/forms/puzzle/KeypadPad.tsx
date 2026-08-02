"use client";

import { Delete } from "lucide-react";
import { useEffect, useState } from "react";

import type { KeypadSpec } from "@/lib/forms/puzzleSpec";

import { PadAction, PadFooter } from "./PadPrimitives";
import type { PadProps } from "./types";

/**
 * A number pad: the person keys a value in and transmits it.
 *
 * Like `choices`, the pad is never told the answer (`solved: null`) — it reports
 * the digits and a workflow decides. Deliberately NOT a text input: an on-screen
 * keyboard on a tablet covers half the screen and takes the puzzle with it.
 */
export function KeypadPad({ spec, disabled, submitLabel, submit }: PadProps<KeypadSpec>) {
  const [entry, setEntry] = useState("");

  // A new puzzle arrives as a new spec; clear whatever was half-keyed for the old one.
  useEffect(() => {
    setEntry("");
  }, [spec]);

  const push = (ch: string) => {
    setEntry((prev) => {
      if (ch === "." && (prev.includes(".") || !prev)) return prev;
      if (ch === "-") return prev.startsWith("-") ? prev.slice(1) : `-${prev}`;
      // The sign doesn't spend one of the person's digits.
      const digits = prev.replace("-", "").replace(".", "").length;
      if (digits >= spec.maxLen) return prev;
      return prev + ch;
    });
  };

  const keys = ["7", "8", "9", "4", "5", "6", "1", "2", "3"];
  const keyClass =
    "min-h-20 rounded-2xl border-2 border-border bg-card text-3xl font-bold transition-all duration-150 hover:border-primary/60 hover:bg-accent active:scale-[0.97] disabled:pointer-events-none disabled:opacity-50 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-ring";

  return (
    <div className="mx-auto w-full max-w-md space-y-4">
      <div className="flex min-h-24 items-center justify-end gap-2 rounded-2xl border-2 border-border bg-muted/40 px-6 font-mono text-5xl font-bold tabular-nums">
        <span>{entry || <span className="text-muted-foreground/40">0</span>}</span>
        {spec.units ? <span className="text-2xl text-muted-foreground">{spec.units}</span> : null}
      </div>

      <div className="grid grid-cols-3 gap-3">
        {keys.map((k) => (
          <button key={k} type="button" disabled={disabled} onClick={() => push(k)} className={keyClass}>
            {k}
          </button>
        ))}
        <button
          type="button"
          disabled={disabled || !spec.allowSign}
          onClick={() => push("-")}
          className={`${keyClass} ${spec.allowSign ? "" : "invisible"}`}
          aria-label="Plus or minus"
        >
          ±
        </button>
        <button type="button" disabled={disabled} onClick={() => push("0")} className={keyClass}>
          0
        </button>
        {spec.allowDecimal ? (
          <button type="button" disabled={disabled} onClick={() => push(".")} className={keyClass}>
            .
          </button>
        ) : (
          <button
            type="button"
            disabled={disabled}
            onClick={() => setEntry((p) => p.slice(0, -1))}
            className={keyClass}
            aria-label="Delete"
          >
            <Delete className="mx-auto h-7 w-7" />
          </button>
        )}
      </div>

      <PadFooter onReset={() => setEntry("")} resetDisabled={disabled || !entry}>
        {spec.allowDecimal ? (
          <button
            type="button"
            disabled={disabled || !entry}
            onClick={() => setEntry((p) => p.slice(0, -1))}
            className="min-h-16 rounded-2xl border px-5 text-muted-foreground hover:bg-muted disabled:opacity-40"
            aria-label="Delete"
          >
            <Delete className="h-6 w-6" />
          </button>
        ) : null}
        <PadAction
          label={submitLabel}
          disabled={disabled || !entry || entry === "-"}
          onClick={() => submit({ solved: null, answer: entry })}
        />
      </PadFooter>
    </div>
  );
}
